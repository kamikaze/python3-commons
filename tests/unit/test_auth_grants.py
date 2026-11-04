from __future__ import annotations

from typing import TYPE_CHECKING

import msgspec
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import HttpUrl

from python3_commons.auth import (
    JWK,
    JWKS,
    OIDCAuthError,
    OIDCClient,
    OIDCError,
    OIDCProviderMetadata,
    OIDCTokenResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_KEY = {'kty': 'RSA', 'kid': 'key-1', 'use': 'sig', 'alg': 'RS256', 'n': 'abc', 'e': 'AQAB', 'x5t#S256': 'thumb'}


class _Idp:
    def __init__(self) -> None:
        self.url = HttpUrl('http://localhost')
        self.forms: list[dict[str, str]] = []
        self.reply: tuple[int, dict[str, object]] = (
            200,
            {'access_token': 'at', 'expires_in': 60, 'token_type': 'Bearer'},
        )
        self.keys: dict[str, object] = {'keys': [_KEY]}


@pytest_asyncio.fixture
async def idp() -> AsyncIterator[_Idp]:
    state = _Idp()

    async def config(request: web.Request) -> web.Response:
        base = f'{request.scheme}://{request.host}'

        return web.json_response(
            {
                'issuer': base,
                'jwks_uri': f'{base}/keys',
                'token_endpoint': f'{base}/token',
                'userinfo_endpoint': f'{base}/userinfo',
                'unknown_field': 1,
            }
        )

    async def keys(_request: web.Request) -> web.Response:
        return web.json_response(state.keys)

    async def token(request: web.Request) -> web.Response:
        state.forms.append({k: str(v) for k, v in (await request.post()).items()})
        status, body = state.reply

        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_get('/.well-known/openid-configuration', config)
    app.router.add_get('/keys', keys)
    app.router.add_post('/token', token)

    async with TestServer(app) as server:
        state.url = HttpUrl(str(server.make_url('')).rstrip('/'))

        yield state


@pytest_asyncio.fixture
async def client(idp: _Idp) -> AsyncIterator[OIDCClient]:
    async with OIDCClient(idp.url, 'jean', 'shh') as oidc_client:
        yield oidc_client


@pytest.mark.asyncio
async def test_client_credentials_posts_scope_and_audience(client: OIDCClient, idp: _Idp) -> None:
    token = await client.fetch_client_credentials_token(scope='a b', audience='api')

    assert idp.forms == [
        {
            'grant_type': 'client_credentials',
            'scope': 'a b',
            'audience': 'api',
            'client_id': 'jean',
            'client_secret': 'shh',
        }
    ]
    assert token.access_token == 'at'
    assert token.id_token is None


@pytest.mark.asyncio
async def test_client_credentials_omits_scope_and_audience_by_default(client: OIDCClient, idp: _Idp) -> None:
    await client.fetch_client_credentials_token()

    assert set(idp.forms[0]) == {'grant_type', 'client_id', 'client_secret'}


@pytest.mark.asyncio
async def test_refresh_token_posts_refresh_grant(client: OIDCClient, idp: _Idp) -> None:
    idp.reply = (200, {'access_token': 'new', 'expires_in': 60, 'token_type': 'Bearer', 'refresh_token': 'r2'})

    token = await client.refresh_token('r1')

    assert idp.forms[0]['grant_type'] == 'refresh_token'
    assert idp.forms[0]['refresh_token'] == 'r1'
    assert token.refresh_token == 'r2'


@pytest.mark.asyncio
async def test_password_grant_still_posts_password_form(client: OIDCClient, idp: _Idp) -> None:
    await client.fetch_token(username='u', password='p')

    assert idp.forms[0] == {
        'grant_type': 'password',
        'username': 'u',
        'password': 'p',
        'scope': 'openid profile email',
        'client_id': 'jean',
        'client_secret': 'shh',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(('error', 'exc_type'), [('invalid_grant', OIDCAuthError), ('server_error', OIDCError)])
async def test_new_grants_share_the_error_mapping(
    client: OIDCClient, idp: _Idp, error: str, exc_type: type[OIDCError]
) -> None:
    idp.reply = (400, {'error': error, 'error_description': 'bad'})

    with pytest.raises(exc_type, match=error):
        await client.refresh_token('r1')

    with pytest.raises(exc_type, match=error):
        await client.fetch_client_credentials_token()


def test_token_response_without_id_token_decodes() -> None:
    token = msgspec.convert({'access_token': 'a', 'expires_in': 1, 'token_type': 'Bearer'}, OIDCTokenResponse)

    assert token.id_token is None


@pytest.mark.asyncio
async def test_typed_metadata_and_key_set_while_dict_accessors_stay_dicts(client: OIDCClient) -> None:
    metadata = await client.get_provider_metadata()
    key_set = await client.get_key_set()
    config = await client.get_config()
    jwks = await client.get_jwks()

    assert isinstance(metadata, OIDCProviderMetadata)
    assert metadata.token_endpoint == config['token_endpoint']
    assert metadata.userinfo_endpoint == config['userinfo_endpoint']
    assert metadata.revocation_endpoint is None
    assert isinstance(key_set, JWKS)
    assert isinstance(key_set.keys[0], JWK)
    assert key_set.keys[0].kid == 'key-1'
    assert key_set.keys[0].x5t_s256 == 'thumb'
    assert type(config) is dict
    assert type(jwks) is dict
    assert jwks['keys'][0]['kid'] == 'key-1'


@pytest.mark.asyncio
async def test_invalid_key_set_raises_oidc_error(client: OIDCClient, idp: _Idp) -> None:
    idp.keys = {'keys': [{'kid': 'no-kty'}]}

    with pytest.raises(OIDCError, match='Invalid JWKS'):
        await client.get_key_set()
