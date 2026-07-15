import logging
import threading
from collections.abc import Sequence
from time import monotonic
from typing import Any, Self, TypeVar

from pydantic import HttpUrl

from python3_commons.helpers import replace_origin

try:
    import aiohttp

    from python3_commons import api_client
except ImportError as e:
    msg = 'Install python3-commons[api-client] to use this feature'
    raise RuntimeError(msg) from e

import msgspec

logger = logging.getLogger(__name__)

DEFAULT_JWKS_CACHE_TTL = 300.0


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

    @property
    def roles(self) -> list[str]:
        roles_list = []

        if self.realm_access:
            roles_list.extend(self.realm_access.get('roles', []))

        if self.resource_access:
            for client in self.resource_access.values():
                roles_list.extend(client.get('roles', []))

        return list(set(roles_list))


T = TypeVar('T', bound=TokenData)


class OIDCTokenResponse(msgspec.Struct):
    access_token: str
    expires_in: int
    id_token: str
    token_type: str
    error: str | None = None
    error_description: str | None = None
    refresh_token: str | None = None
    scope: str | None = None


class OIDCError(Exception):
    pass


class OIDCAuthError(OIDCError):
    pass


class OIDCClient:
    def __init__(
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
    ) -> None:
        if authority_internal_host:
            authority_url = replace_origin(authority_url, authority_internal_host)

        self._authority_url = authority_url
        self._authority_internal_host = authority_internal_host
        self._client_id = client_id
        self._client_secret = client_secret

        self._connection_limit = connection_limit
        self._session: aiohttp.ClientSession | None = None
        self._timeout = timeout
        self._verify_cert = verify_cert

        self._session_lock = threading.Lock()
        self._config: dict[str, Any] | None = None
        self._config_lock = threading.Lock()
        self._jwks: dict[str, Any] | None = None
        self._jwks_lock = threading.Lock()
        self._jwks_cache_ttl = jwks_cache_ttl
        self._jwks_fetched_at: float | None = None
        self._audit_name: str | None = audit_name

    def _get_session(self) -> aiohttp.ClientSession:
        if (session := self._session) and not session.closed:
            return session

        with self._session_lock:
            if (session := self._session) and not session.closed:
                return session

            connector = aiohttp.TCPConnector(verify_ssl=self._verify_cert, limit=self._connection_limit)
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            self._session = session

            return session

    async def __aenter__(self) -> Self:
        self._get_session()

        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.close()

    async def _fetch_config(self) -> dict:
        """
        Fetch the OpenID configuration (including JWKS URI) from OIDC authority.
        """
        async with api_client.request(
            self._get_session(),
            str(self._authority_url),
            '/.well-known/openid-configuration',
            audit_name=self._audit_name,
        ) as response:
            return await response.json()

    async def get_config(self) -> dict[str, Any]:
        if config := self._config:
            return config

        with self._config_lock:
            if config := self._config:
                return config

            config = await self._fetch_config()
            self._config = config

            return config

    async def _fetch_jwks(self, jwks_uri: str) -> dict[str, Any]:
        """
        Fetch the JSON Web Key Set (JWKS) for validating the token's signature.
        """
        if authority_internal_host := self._authority_internal_host:
            logger.debug('Received jwks_uri: %s', jwks_uri)
            logger.debug('Replacing OIDC authority host with: %s', authority_internal_host)
            jwks_uri = str(replace_origin(HttpUrl(jwks_uri), authority_internal_host))
            logger.debug('Modified jwks_uri: %s', jwks_uri)

        async with api_client.request(self._get_session(), jwks_uri, '', audit_name=self._audit_name) as response:
            return await response.json()

    def _is_fresh_jwks(self) -> bool:
        fetched_at = self._jwks_fetched_at

        if self._jwks is None or fetched_at is None:
            return False

        return (monotonic() - fetched_at) < self._jwks_cache_ttl

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        if not force_refresh and (jwks := self._jwks) and self._is_fresh_jwks():
            return jwks

        fetched_at = self._jwks_fetched_at

        with self._jwks_lock:
            if (jwks := self._jwks) and self._jwks_fetched_at != fetched_at:
                return jwks

            if not force_refresh and jwks and self._is_fresh_jwks():
                return jwks

            oidc_config = await self.get_config()

            jwks = await self._fetch_jwks(oidc_config['jwks_uri'])
            self._jwks = jwks
            self._jwks_fetched_at = monotonic()

            return jwks

    async def fetch_token(
        self,
        *,
        username: str,
        password: str,
        scope: str = 'openid profile email',
    ) -> OIDCTokenResponse:
        data = {
            'grant_type': 'password',
            'username': username,
            'password': password,
            'client_id': self._client_id,
            'scope': scope,
        }

        if self._client_secret:
            data['client_secret'] = self._client_secret

        openid_config = await self.get_config()

        try:
            async with api_client.request(
                self._get_session(),
                openid_config['token_endpoint'],
                '',
                method='post',
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                audit_name=self._audit_name,
            ) as response:
                payload = await response.read()

                try:
                    body = msgspec.json.decode(payload)
                except Exception as e:
                    msg = f'Non-JSON response from OIDC provider: {payload[:300]!r}'
                    raise OIDCError(msg) from e

                if response.status >= 400:
                    error = None
                    description = None

                    if isinstance(body, dict):
                        error = body.get('error') or body.get('code')
                        description = body.get('error_description') or body.get('message') or ''

                    error = error or f'http_{response.status}'

                    if error in {'invalid_grant', 'invalid_client'}:
                        msg = f'{error}: {description}'
                        raise OIDCAuthError(msg)

                    msg = f'{error}: {description}'
                    raise OIDCError(msg)

                decoder = msgspec.json.Decoder(OIDCTokenResponse)

                return decoder.decode(payload)

        except TimeoutError as e:
            msg = 'OIDC request timed out'
            raise OIDCError(msg) from e
        except aiohttp.ClientError as e:
            msg = 'OIDC transport error'
            raise OIDCError(msg) from e
