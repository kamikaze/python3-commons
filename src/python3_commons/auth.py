import logging
import threading
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any, Self, TypeVar

from pydantic import HttpUrl

from python3_commons.helpers import replace_origin

try:
    import aiohttp
except ImportError as e:
    msg = 'Install python3-commons[authn] to use this feature'
    raise RuntimeError(msg) from e

import msgspec

logger = logging.getLogger(__name__)
_OIDC_CONFIG_LOCK = threading.Lock()
_OIDC_JWKS_LOCK = threading.Lock()
_OIDC_SESSION_LOCK = threading.Lock()


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
    token_type: str
    expires_in: int
    refresh_token: str
    scope: str
    id_token: str
    error: str | None = None
    error_description: str | None = None


class OIDCError(Exception):
    pass


class OIDCAuthError(OIDCError):
    pass


# TODO: use api_client
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
        authority_internal_host: HttpUrl | None = None,
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

        self._config: Mapping[str, Any] | None = None
        self._jwks: Mapping[str, Any] | None = None

    def get_session(self) -> aiohttp.ClientSession:
        if self._session:
            return self._session

        with _OIDC_SESSION_LOCK:
            if self._session:
                return self._session

            connector = aiohttp.TCPConnector(verify_ssl=self._verify_cert, limit=self._connection_limit)
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            self._session = session

            return session

    async def __aenter__(self) -> Self:
        self.get_session()

        return self

    async def __aexit__(self, *_: object) -> None:
        if self._session:
            await self._session.close()

    async def _fetch_config(self) -> dict:
        """
        Fetch the OpenID configuration (including JWKS URI) from OIDC authority.
        """
        if self._session is None:
            msg = 'ClientSession not initialized'
            raise RuntimeError(msg)

        oidc_config_url = f'{self._authority_url}/.well-known/openid-configuration'

        logger.debug('Fetching OpenID configuration from: %s', oidc_config_url)

        async with self._session.get(oidc_config_url) as response:
            if response.status != HTTPStatus.OK:
                _msg = 'Failed to fetch OpenID configuration'

                raise RuntimeError(_msg)

            return await response.json()

    async def get_config(self) -> Mapping[str, Any]:
        if self._config:
            return self._config

        with _OIDC_CONFIG_LOCK:
            if self._config:
                return self._config

            config = await self._fetch_config()
            self._config = config

            return config

    async def _fetch_jwks(self, jwks_uri: str) -> dict:
        """
        Fetch the JSON Web Key Set (JWKS) for validating the token's signature.
        """
        if self._session is None:
            msg = 'ClientSession not initialized'
            raise RuntimeError(msg)

        if authority_internal_host := self._authority_internal_host:
            logger.debug('Received jwks_uri: %s', jwks_uri)
            logger.debug('Replacing OIDC authority host with: %s', authority_internal_host)
            jwks_uri = str(replace_origin(HttpUrl(jwks_uri), authority_internal_host))
            logger.debug('Modified jwks_uri: %s', jwks_uri)

        async with self._session.get(jwks_uri) as response:
            if response.status != HTTPStatus.OK:
                _msg = 'Failed to fetch JWKS'

                raise RuntimeError(_msg)

            return await response.json()

    async def get_jwks(self) -> Mapping[str, Any]:
        if self._jwks:
            return self._jwks

        with _OIDC_JWKS_LOCK:
            if self._jwks:
                return self._jwks

            oidc_config = await self.get_config()

            jwks = await self._fetch_jwks(oidc_config['jwks_uri'])
            self._jwks = jwks

            return jwks

    async def fetch_token(
        self,
        *,
        username: str,
        password: str,
        scope: str = 'openid profile email',
    ) -> OIDCTokenResponse:
        if self._session is None:
            msg = 'ClientSession not initialized'
            raise RuntimeError(msg)

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
            async with self._session.post(
                openid_config['token_endpoint'],
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
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
