from __future__ import annotations

import logging
from collections.abc import Sequence
from http import HTTPStatus
from typing import TypeVar

import aiohttp
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


async def fetch_openid_config() -> dict:
    """
    Fetch the OpenID configuration (including JWKS URI) from OIDC authority.
    """
    async with aiohttp.ClientSession() as session, session.get(OIDC_CONFIG_URL) as response:
        if response.status != HTTPStatus.OK:
            msg = 'Failed to fetch OpenID configuration'

            raise RuntimeError(msg)

        return await response.json()


async def fetch_jwks(jwks_uri: str) -> dict:
    """
    Fetch the JSON Web Key Set (JWKS) for validating the token's signature.
    """
    async with aiohttp.ClientSession() as session, session.get(jwks_uri) as response:
        if response.status != HTTPStatus.OK:
            msg = 'Failed to fetch JWKS'

            raise RuntimeError(msg)

        return await response.json()
