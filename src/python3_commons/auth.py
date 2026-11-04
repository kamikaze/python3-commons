import asyncio
import logging
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from http import HTTPStatus
from time import monotonic
from typing import Any, Self

from pydantic import HttpUrl, SecretStr

from python3_commons._extras import require
from python3_commons.urls import replace_origin

require('authn', 'aiohttp')

# PEP 810 (3.15): `import python3_commons.auth` does not import aiohttp until its first use; 3.14 ignores this list
# and imports eagerly. Nothing at module level may use `aiohttp` (no base classes, no defaults).
__lazy_modules__ = ['aiohttp']

import aiohttp
import msgspec

from python3_commons import api_client
from python3_commons.conf import OIDCSettings, get_oidc_settings

__all__ = [
    'DEFAULT_JWKS_CACHE_TTL',
    'DEFAULT_MIN_REFRESH_INTERVAL',
    'JWK',
    'JWKS',
    'OIDCAuthError',
    'OIDCClient',
    'OIDCError',
    'OIDCProviderMetadata',
    'OIDCTokenResponse',
    'TokenData',
]
logger = logging.getLogger(__name__)

DEFAULT_JWKS_CACHE_TTL = 300.0
DEFAULT_MIN_REFRESH_INTERVAL = 60.0
# First retry delay after a failed JWKS refresh; doubles per consecutive failure up to min_refresh_interval.
JWKS_BACKOFF_BASE = 1.0
# Discovery-document fields that hold endpoints; rewritten to authority_internal_host when it is set.
ENDPOINT_KEYS = (
    'token_endpoint',
    'jwks_uri',
    'userinfo_endpoint',
    'introspection_endpoint',
    'revocation_endpoint',
    'end_session_endpoint',
)


class TokenData(msgspec.Struct):
    exp: int
    iat: int
    iss: str
    sub: str
    aud: str | Sequence[str] | None = None
    email: str | None = None
    name: str | None = None
    preferred_username: str | None = None
    realm_access: dict[str, Sequence[str]] | None = None
    resource_access: dict[str, dict[str, Sequence[str]]] | None = None
    # Appended after the 0.29 fields, so positional construction keeps working.
    nbf: int | None = None
    azp: str | None = None
    scope: str | None = None
    jti: str | None = None
    sid: str | None = None
    groups: list[str] = msgspec.field(default_factory=list)

    @property
    def roles(self) -> list[str]:
        roles = set((self.realm_access or {}).get('roles', ()))

        for client in (self.resource_access or {}).values():
            roles.update(client.get('roles', ()))

        return list(roles)

    def has_role(self, role: str) -> bool:
        return role in self.roles


class OIDCTokenResponse(msgspec.Struct):
    access_token: str
    expires_in: int
    # Client-credentials and refresh responses carry no id_token. ``token_type`` gets a default only so that
    # ``id_token`` can have one without moving fields (positional construction keeps working).
    id_token: str | None = None
    token_type: str = 'Bearer'  # noqa: S105
    error: str | None = None
    error_description: str | None = None
    refresh_token: str | None = None
    scope: str | None = None


class OIDCProviderMetadata(msgspec.Struct):
    """Typed view of the discovery document (``get_provider_metadata()``). Unknown fields are ignored."""

    issuer: str
    jwks_uri: str
    token_endpoint: str
    authorization_endpoint: str | None = None
    userinfo_endpoint: str | None = None
    introspection_endpoint: str | None = None
    revocation_endpoint: str | None = None
    end_session_endpoint: str | None = None
    registration_endpoint: str | None = None
    grant_types_supported: list[str] | None = None
    response_types_supported: list[str] | None = None
    scopes_supported: list[str] | None = None
    subject_types_supported: list[str] | None = None
    id_token_signing_alg_values_supported: list[str] | None = None
    token_endpoint_auth_methods_supported: list[str] | None = None
    code_challenge_methods_supported: list[str] | None = None


class JWK(msgspec.Struct):
    """One JSON Web Key (RFC 7517). Which of the key material fields are set depends on ``kty``."""

    kty: str
    kid: str | None = None
    use: str | None = None
    alg: str | None = None
    key_ops: list[str] | None = None
    n: str | None = None
    e: str | None = None
    crv: str | None = None
    x: str | None = None
    y: str | None = None
    x5c: list[str] | None = None
    x5t: str | None = None
    x5t_s256: str | None = msgspec.field(default=None, name='x5t#S256')


class JWKS(msgspec.Struct):
    keys: list[JWK]


def _find_key(jwks: Mapping[str, Any], kid: str) -> Mapping[str, Any] | None:
    for key in jwks.get('keys') or ():
        if isinstance(key, Mapping) and key.get('kid') == kid:
            return key

    return None


class OIDCError(Exception):
    pass


class OIDCAuthError(OIDCError):
    pass


class OIDCClient:
    """
    OpenID Connect client: discovery document, JWKS and tokens (password, client-credentials and refresh grants).

    Lifecycle (convention 5, A3). Preferred: create one client per event loop in the application lifespan, let every
    caller use it directly and call ``await client.aclose()`` on shutdown. ``async with client`` per call (the
    fastapi-commons pattern) stays supported: the session is shared and reference-counted, but it is rebuilt every
    time concurrency drops to zero, so the lifespan pattern is the one without session churn.

    ``session=`` injects a borrowed ``aiohttp.ClientSession``: the client never closes it and never replaces it.
    Without it the client owns a session that it creates lazily.

    JWKS refresh policy (A8, A2). Refreshes are single-flight: concurrent callers share one library-owned Task
    (created in a copy of the first caller's context, so context-variable changes made inside it do not reach
    callers) and await it through ``asyncio.shield``, so cancelling a caller never cancels the refresh for the others.
    Keys are served only while younger than ``jwks_cache_ttl``; expired keys are never served, even when the IdP is
    down. A failed refresh is remembered: until the backoff ends, callers that need a refresh get
    ``ConnectionRefusedError('OIDC provider unavailable')`` chained from the last failure and the IdP is not
    contacted. The backoff starts at 1 s, doubles per consecutive failure and is capped at ``min_refresh_interval``
    (60 s by default); it resets on success. ``min_refresh_interval`` also rate-limits ``force_refresh=True``: a
    forced refresh within that many seconds of the previous refresh attempt (or while one is running) returns the
    cached keys instead of contacting the IdP. ``0`` disables both. Endpoints in the discovery document are rewritten
    to ``authority_internal_host`` when that is set, so ``get_config()`` returns the rewritten endpoints.
    """

    # State machine (asyncio only, no threads, A9). Nothing below awaits between reading and writing these fields,
    # so single-threaded asyncio makes every transition atomic.
    #
    #   _users      number of tasks inside ``async with client``.
    #   _inflight   number of public network calls (get_config, get_jwks, the token methods) currently running, counted
    #               from entry to exit by ``_busy()``, including the time spent waiting on a lock or a refresh,
    #               plus one slot held by the JWKS refresh Task (see ``_jwks_task``).
    #   _session    the session in use, or None. Owned sessions are created lazily by ``_get_session()``.
    #   _owns_session  False when the caller injected ``session=``; then the session is never closed or replaced.
    #   _loop       the loop the session, the config lock and the JWKS task were built on (None until first use).
    #   _jwks_task  the single in-flight JWKS refresh Task, or None. It is a library-owned Task that callers await
    #               through asyncio.shield. ``_inflight`` is incremented when it is created and decremented by its
    #               done callback (which also clears ``_jwks_task`` and retrieves the exception), so the session
    #               stays open for as long as the refresh runs, even after every caller was cancelled or left
    #               ``async with``. Nobody cancels it except loop shutdown; it is bounded by ``timeout``.
    #   _jwks_attempted_at / _jwks_failed_at / _jwks_failures / _jwks_failure
    #               end time of the last refresh attempt, end time of the last failed attempt (None after a success),
    #               consecutive failures, and the last exception (the __cause__ of the backoff error).
    #   get_jwks()  needs a refresh when the cache is expired or force_refresh is not rate-limited. With a refresh
    #               task running it joins it; otherwise, inside the backoff window it raises ConnectionRefusedError;
    #               otherwise it starts a new task. Success sets the cache and clears the failure state; failure
    #               records it and re-raises the original error to the callers that were waiting.
    #
    # __aenter__    _users += 1 and make sure a session exists.
    # __aexit__     _users -= 1; closes the owned session only when _users == 0 and _inflight == 0. Otherwise the
    #               last busy party leaves it open: the next ``async with`` exit or ``aclose()`` closes it.
    # aclose()      closes the owned session unconditionally (the lifespan hook). Idempotent. Callers still inside
    #               are cut off, by design. The session is detached (``_session = None``) *before* the first await,
    #               so a task that arrives while the close is running gets a fresh session instead of the dying one.
    # loop change   ``_bind_loop()`` runs at the start of every entry point. When the running loop is not ``_loop``:
    #               busy (_users or _inflight non-zero) on a loop that is still open -> RuntimeError;
    #               otherwise idle -> rebuild the owned session and the config lock on the new loop and forget the
    #               JWKS task (idle means no task is running; a task of a closed loop can never finish). An owned
    #               session that is still open is dropped with a WARNING, because it cannot be closed from another
    #               loop. If the old loop is closed, its tasks can never finish, so counts left by them are reset.
    #               The cached config and JWKS are plain data and survive.

    def __init__(  # noqa: PLR0913  # public signature is pinned (C1/C2)
        self,
        authority_url: HttpUrl,
        client_id: str,
        client_secret: str | None = None,
        *,
        timeout: float = 10.0,
        verify_cert: bool = True,
        connection_limit: int = 100,
        jwks_cache_ttl: float = DEFAULT_JWKS_CACHE_TTL,
        authority_internal_host: HttpUrl | None = None,
        audit_name: str | None = None,
        session: aiohttp.ClientSession | None = None,
        min_refresh_interval: float = DEFAULT_MIN_REFRESH_INTERVAL,
    ) -> None:
        if min_refresh_interval < 0:
            msg = 'min_refresh_interval must not be negative'

            raise ValueError(msg)

        if authority_internal_host:
            authority_url = replace_origin(authority_url, authority_internal_host)

        self._authority_url = authority_url
        self._authority_internal_host = authority_internal_host
        self._client_id = client_id
        # Held as SecretStr so repr() and debug output of the client never show it; unwrapped only for the token POST
        self._client_secret: SecretStr | None = SecretStr(client_secret) if client_secret else None

        self._connection_limit = connection_limit
        self._session: aiohttp.ClientSession | None = session
        self._owns_session = session is None
        self._timeout = timeout
        self._verify_cert = verify_cert

        self._loop: asyncio.AbstractEventLoop | None = None
        self._users = 0
        self._inflight = 0
        self._config: dict[str, Any] | None = None
        self._config_lock = asyncio.Lock()
        self._jwks: dict[str, Any] | None = None
        self._jwks_task: asyncio.Task[dict[str, Any]] | None = None
        self._jwks_cache_ttl = jwks_cache_ttl
        self._jwks_fetched_at: float | None = None
        self._min_refresh_interval = min_refresh_interval
        self._jwks_attempted_at: float | None = None
        self._jwks_failed_at: float | None = None
        self._jwks_failures = 0
        self._jwks_failure: Exception | None = None
        self._audit_name: str | None = audit_name

    @classmethod
    def from_settings(cls, settings: OIDCSettings | None = None, **overrides: Any) -> Self:
        """
        Build a client from ``OIDCSettings`` (default: the shared ``get_oidc_settings()``).

        ``authority_url`` and ``client_id`` must be set, either in the settings or as overrides; otherwise
        ``ValueError`` is raised. ``jwks_refresh_interval`` maps to ``min_refresh_interval``. Keyword ``overrides``
        are passed to the constructor and win over the settings.
        """
        if settings is None:
            settings = get_oidc_settings()

        kwargs: dict[str, Any] = {
            'authority_url': settings.authority_url,
            'client_id': settings.client_id,
            'client_secret': settings.client_secret.get_secret_value() or None,
            'timeout': settings.timeout,
            'verify_cert': settings.verify_cert,
            'connection_limit': settings.connection_limit,
            'jwks_cache_ttl': settings.jwks_cache_ttl,
            'authority_internal_host': settings.authority_internal_host,
            'min_refresh_interval': settings.jwks_refresh_interval,
        }
        kwargs.update(overrides)

        if kwargs['authority_url'] is None or kwargs['client_id'] is None:
            msg = 'OIDC authority_url and client_id must be set'

            raise ValueError(msg)

        return cls(**kwargs)

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        old_loop = self._loop

        if old_loop is loop:
            return

        if old_loop is not None:
            if (self._users or self._inflight) and not old_loop.is_closed():
                msg = 'OIDCClient is in use on another event loop'

                raise RuntimeError(msg)

            self._users = 0
            self._inflight = 0
            self._config_lock = asyncio.Lock()
            self._jwks_task = None

            if self._owns_session and (stale := self._session) is not None:
                self._session = None

                if not stale.closed:
                    logger.warning('Dropping an unclosed OIDCClient session that belongs to another event loop')

        self._loop = loop

    @contextmanager
    def _busy(self) -> Generator[None]:
        self._bind_loop()
        self._inflight += 1

        try:
            yield
        finally:
            self._inflight -= 1

    async def _get_session(self) -> aiohttp.ClientSession:
        self._bind_loop()

        if (session := self._session) is not None and (not self._owns_session or not session.closed):
            return session

        connector = aiohttp.TCPConnector(ssl=self._verify_cert, limit=self._connection_limit)
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        self._session = session

        return session

    async def _close_owned_session(self) -> None:
        if not self._owns_session or (session := self._session) is None:
            return

        self._session = None

        await session.close()

    async def aclose(self) -> None:
        """Close the owned session now, even if other tasks are still using it. Idempotent; the lifespan hook."""
        await self._close_owned_session()

    async def __aenter__(self) -> Self:
        await self._get_session()
        self._users += 1

        return self

    async def __aexit__(self, *_: object) -> None:
        self._users = max(0, self._users - 1)

        if self._users == 0 and self._inflight == 0:
            await self._close_owned_session()

    async def _fetch_config(self) -> dict[str, Any]:
        """
        Fetch the OpenID configuration (including JWKS URI) from OIDC authority.
        """
        async with (
            asyncio.timeout(self._timeout),
            api_client.request(
                await self._get_session(),
                str(self._authority_url),
                '/.well-known/openid-configuration',
                audit_name=self._audit_name,
            ) as response,
        ):
            return await response.json()

    def _rewrite_endpoints(self, config: dict[str, Any]) -> dict[str, Any]:
        """Point every discovered endpoint at ``authority_internal_host`` (split-horizon deployments)."""
        if (internal_host := self._authority_internal_host) is None:
            return config

        rewritten = dict(config)

        for key in ENDPOINT_KEYS:
            if not (endpoint := config.get(key)) or not isinstance(endpoint, str):
                continue

            try:
                rewritten[key] = str(replace_origin(HttpUrl(endpoint), internal_host))
            except ValueError:
                logger.warning('Cannot rewrite OIDC %s to the internal host; keeping it as discovered', key)

        return rewritten

    async def get_config(self) -> dict[str, Any]:
        with self._busy():
            if config := self._config:
                return config

            async with self._config_lock:
                if config := self._config:
                    return config

                config = self._rewrite_endpoints(await self._fetch_config())
                self._config = config

                return config

    async def get_provider_metadata(self) -> OIDCProviderMetadata:
        """The discovery document as a typed struct (endpoints already rewritten, like ``get_config()``)."""
        config = await self.get_config()

        try:
            return msgspec.convert(config, OIDCProviderMetadata)
        except msgspec.ValidationError as e:
            msg = f'Invalid OIDC discovery document: {e}'

            raise OIDCError(msg) from e

    async def _fetch_jwks(self, jwks_uri: str) -> dict[str, Any]:
        """
        Fetch the JSON Web Key Set (JWKS) for validating the token's signature.
        """
        async with (
            asyncio.timeout(self._timeout),
            api_client.request(await self._get_session(), jwks_uri, '', audit_name=self._audit_name) as response,
        ):
            return await response.json()

    def _is_fresh_jwks(self) -> bool:
        fetched_at = self._jwks_fetched_at

        if self._jwks is None or fetched_at is None:
            return False

        return (monotonic() - fetched_at) < self._jwks_cache_ttl

    def _backoff_active(self) -> bool:
        if (failed_at := self._jwks_failed_at) is None:
            return False

        backoff = min(self._min_refresh_interval, JWKS_BACKOFF_BASE * 2 ** max(0, self._jwks_failures - 1))

        return (monotonic() - failed_at) < backoff

    def _force_refresh_limited(self) -> bool:
        if (interval := self._min_refresh_interval) <= 0:
            return False

        task = self._jwks_task

        if task is not None and not task.done():
            return True

        return (attempted_at := self._jwks_attempted_at) is not None and (monotonic() - attempted_at) < interval

    async def _refresh_jwks(self) -> dict[str, Any]:
        try:
            oidc_config = await self.get_config()
            jwks = await self._fetch_jwks(oidc_config['jwks_uri'])
        except Exception as e:
            self._jwks_failure = e
            self._jwks_failures += 1
            self._jwks_failed_at = self._jwks_attempted_at = monotonic()

            raise

        self._jwks = jwks
        self._jwks_fetched_at = self._jwks_attempted_at = monotonic()
        self._jwks_failed_at = None
        self._jwks_failures = 0
        self._jwks_failure = None

        return jwks

    def _on_jwks_refresh_done(self, task: asyncio.Task[dict[str, Any]]) -> None:
        self._inflight = max(0, self._inflight - 1)

        if self._jwks_task is task:
            self._jwks_task = None

        if not task.cancelled():
            task.exception()  # mark retrieved: every caller may have been cancelled

    def _start_jwks_refresh(self) -> asyncio.Task[dict[str, Any]]:
        task = asyncio.get_running_loop().create_task(self._refresh_jwks(), name='oidc-jwks-refresh')
        self._inflight += 1
        self._jwks_task = task
        task.add_done_callback(self._on_jwks_refresh_done)

        return task

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        with self._busy():
            jwks = self._jwks

            if jwks is not None and self._is_fresh_jwks() and (not force_refresh or self._force_refresh_limited()):
                return jwks

            task = self._jwks_task

            if task is None or task.done():
                if self._backoff_active():
                    msg = 'OIDC provider unavailable'

                    raise ConnectionRefusedError(msg) from self._jwks_failure

                task = self._start_jwks_refresh()

            return await asyncio.shield(task)

    async def get_key_set(self, *, force_refresh: bool = False) -> JWKS:
        """The JWKS as a typed struct; refresh rules are those of ``get_jwks()``."""
        jwks = await self.get_jwks(force_refresh=force_refresh)

        try:
            return msgspec.convert(jwks, JWKS)
        except msgspec.ValidationError as e:
            msg = f'Invalid JWKS document: {e}'

            raise OIDCError(msg) from e

    async def get_signing_key(self, kid: str) -> Mapping[str, Any] | None:
        """
        Return the JWK with this ``kid``, or None when the IdP does not publish it.

        An unknown kid triggers at most one refresh per ``min_refresh_interval`` (a key rotation); inside the interval
        the cached keys are searched again and None is returned. If the keys have expired and the IdP is down, the
        ``ConnectionRefusedError`` from ``get_jwks`` propagates instead of an answer from stale keys.
        """
        if key := _find_key(await self.get_jwks(), kid):
            return key

        return _find_key(await self.get_jwks(force_refresh=True), kid)

    async def fetch_token(
        self,
        *,
        username: str,
        password: str,
        scope: str = 'openid profile email',
    ) -> OIDCTokenResponse:
        """Password (ROPC) grant."""
        form = {'grant_type': 'password', 'username': username, 'password': password, 'scope': scope}

        with self._busy():
            return await self._token_request(form)

    async def fetch_client_credentials_token(
        self,
        *,
        scope: str | None = None,
        audience: str | None = None,
    ) -> OIDCTokenResponse:
        """Client-credentials grant (service to service). ``scope`` and ``audience`` are sent only when given."""
        form = {'grant_type': 'client_credentials'}

        if scope is not None:
            form['scope'] = scope

        if audience is not None:
            form['audience'] = audience

        with self._busy():
            return await self._token_request(form)

    async def refresh_token(self, refresh_token: str) -> OIDCTokenResponse:
        """Refresh-token grant."""
        form = {'grant_type': 'refresh_token', 'refresh_token': refresh_token}

        with self._busy():
            return await self._token_request(form)

    async def _token_request(self, form: Mapping[str, str]) -> OIDCTokenResponse:
        """POST a grant with the client credentials added and map errors (the caller holds ``_busy``)."""
        data = {**form, 'client_id': self._client_id}

        if self._client_secret is not None:
            data['client_secret'] = self._client_secret.get_secret_value()

        openid_config = await self.get_config()

        try:
            async with (
                asyncio.timeout(self._timeout),
                api_client.request(
                    await self._get_session(),
                    openid_config['token_endpoint'],
                    '',
                    method='post',
                    data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    audit_name=self._audit_name,
                    raise_for_status=False,
                ) as response,
            ):
                payload = await response.read()

                try:
                    body = msgspec.json.decode(payload)
                except Exception as e:
                    msg = f'Non-JSON response from OIDC provider: {payload[:300]!r}'
                    raise OIDCError(msg) from e

                if response.status >= HTTPStatus.BAD_REQUEST:
                    error = None
                    description = None

                    if isinstance(body, dict):
                        error = body.get('error') or body.get('code')
                        description = body.get('error_description') or body.get('message') or ''

                    error = error or f'http_{response.status}'
                    exc_type = OIDCAuthError if error in {'invalid_grant', 'invalid_client'} else OIDCError
                    msg = f'{error}: {description}'

                    raise exc_type(msg)

                return msgspec.convert(body, OIDCTokenResponse)

        except TimeoutError as e:
            msg = 'OIDC request timed out'
            raise OIDCError(msg) from e
        except aiohttp.ClientError as e:
            msg = 'OIDC transport error'
            raise OIDCError(msg) from e
