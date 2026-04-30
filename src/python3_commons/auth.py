from __future__ import annotations

import logging
from collections.abc import Sequence
from http import HTTPStatus
from typing import Self, TypeVar

try:
    import aiohttp
except ImportError as e:
    msg = 'Install python3-commons[authn] to use this feature'
    raise RuntimeError(msg) from e

import msgspec

from python3_commons.conf import oidc_settings

logger = logging.getLogger(__name__)


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
OIDC_CONFIG_URL = (
    f'{oidc_settings.authority_internal_url or oidc_settings.authority_url}/.well-known/openid-configuration'
)


class OIDCTokenResponse(msgspec.Struct):
    access_token: str
    token_type: str
    expires_in: int
    refresh_token: str
    scope: str
    id_token: str
    error: str | None = None
    error_description: str | None = None


async def fetch_openid_config() -> dict:
    """
    Fetch the OpenID configuration (including JWKS URI) from OIDC authority.
    """
    async with aiohttp.ClientSession() as session, session.get(OIDC_CONFIG_URL) as response:
        if response.status != HTTPStatus.OK:
            _msg = 'Failed to fetch OpenID configuration'

            raise RuntimeError(_msg)

        return await response.json()


async def fetch_jwks(jwks_uri: str) -> dict:
    """
    Fetch the JSON Web Key Set (JWKS) for validating the token's signature.
    """
    if oidc_settings.authority_internal_url:
        jwks_uri = jwks_uri.replace(str(oidc_settings.authority_url), str(oidc_settings.authority_internal_url))

    async with aiohttp.ClientSession() as session, session.get(jwks_uri) as response:
        if response.status != HTTPStatus.OK:
            _msg = 'Failed to fetch JWKS'

            raise RuntimeError(_msg)

        return await response.json()


class OIDCError(Exception):
    pass


class OIDCAuthError(OIDCError):
    pass


# TODO: use api_client
class OIDCClient:
    def __init__(
        self,
        authority_url: str,
        client_id: str,
        client_secret: str | None = None,
        *,
        timeout: float = 10.0,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._token_url = f'{authority_url}/protocol/openid-connect/token'  # TODO: get it from openid-configuration
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> Self:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_session and self._session:
            await self._session.close()

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

        try:
            async with self._session.post(
                self._token_url,
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            ) as resp:
                payload = await resp.read()
                decoder = msgspec.json.Decoder(type=OIDCTokenResponse)
                token = decoder.decode(payload)

        except TimeoutError as e:
            msg = 'OIDC request timed out'

            raise OIDCError(msg) from e
        except aiohttp.ClientError as e:
            msg = 'OIDC transport error'

            raise OIDCError(msg) from e

        if not resp.ok:
            error = token.error
            description = token.error_description

            if error in {'invalid_grant', 'invalid_client'}:
                msg = f'{error}: {description}'

                raise OIDCAuthError(msg)

            msg = f'{error}: {description}'

            raise OIDCError(msg)

        return token
