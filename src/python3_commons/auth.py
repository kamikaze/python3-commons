import logging
from http import HTTPStatus
from typing import Annotated, Any, Callable, Coroutine, Sequence, Type, TypeVar

import aiohttp
import msgspec
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from python3_commons.conf import oidc_settings

logger = logging.getLogger(__name__)


class TokenData(msgspec.Struct):
    sub: str
    aud: str | Sequence[str]
    exp: int
    iss: str


T = TypeVar('T', bound=TokenData)

OIDC_CONFIG_URL = f'{oidc_settings.authority_url}/.well-known/openid-configuration'
_JWKS: dict | None = None

bearer_security = HTTPBearer(auto_error=oidc_settings.enabled)


async def fetch_openid_config() -> dict:
    """
    Fetch the OpenID configuration (including JWKS URI) from OIDC authority.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(OIDC_CONFIG_URL) as response:
            if response.status != HTTPStatus.OK:
                raise HTTPException(
                    status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail='Failed to fetch OpenID configuration'
                )

            return await response.json()


async def fetch_jwks(jwks_uri: str) -> dict:
    """
    Fetch the JSON Web Key Set (JWKS) for validating the token's signature.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(jwks_uri) as response:
            if response.status != HTTPStatus.OK:
                raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, detail='Failed to fetch JWKS')

            return await response.json()


def get_token_verifier(token_cls: Type[T]) -> Callable[[HTTPAuthorizationCredentials], Coroutine[Any, Any, T | None]]:
    async def get_verified_token(
        authorization: Annotated[HTTPAuthorizationCredentials, Depends(bearer_security)],
    ) -> T | None:
        """
        Verify the JWT access token using OIDC authority JWKS.
        """
        global _JWKS

        if not oidc_settings.enabled:
            return None

        token = authorization.credentials

        try:
            if not _JWKS:
                openid_config = await fetch_openid_config()
                _JWKS = await fetch_jwks(openid_config['jwks_uri'])

            if oidc_settings.client_id:
                payload = jwt.decode(token, _JWKS, algorithms=['RS256'], audience=oidc_settings.client_id)
            else:
                payload = jwt.decode(token, _JWKS, algorithms=['RS256'])

            token_data = token_cls(**payload)

            return token_data
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail='Token has expired')
        except JWTError as e:
            raise HTTPException(status_code=HTTPStatus.UNAUTHORIZED, detail=f'Token is invalid: {str(e)}')

    return get_verified_token
