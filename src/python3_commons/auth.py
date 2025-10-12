import logging
from collections.abc import Sequence
from http import HTTPStatus
from typing import TypeVar

import aiohttp
import msgspec

from python3_commons.conf import oidc_settings

logger = logging.getLogger(__name__)


class TokenData(msgspec.Struct):
    sub: str
    aud: str | Sequence[str]
    exp: int
    iss: str


T = TypeVar('T', bound=TokenData)
OIDC_CONFIG_URL = f'{oidc_settings.authority_url}/.well-known/openid-configuration'


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
