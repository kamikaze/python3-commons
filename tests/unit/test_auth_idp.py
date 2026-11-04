"""OIDCClient against a real HTTP identity provider (aiohttp TestServer); nothing in the client is patched.

Time is controlled through ``python3_commons.auth.monotonic`` (the JWKS TTL, backoff and rate limit clock). Waiting
is done on Events, never on the wall clock.
"""

import asyncio
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import aiohttp
import msgspec
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import HttpUrl

from python3_commons.auth import ENDPOINT_KEYS, OIDCAuthError, OIDCClient, OIDCError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from pytest_mock import MockerFixture

_WAIT_SECONDS: Final = 5.0
_TOKEN_BODY: Final = {'access_token': 'at', 'expires_in': 60, 'token_type': 'Bearer'}
_KEYS_V1: Final = {'keys': [{'kty': 'RSA', 'kid': 'key-1'}]}
_KEYS_V2: Final = {'keys': [{'kty': 'RSA', 'kid': 'key-2'}]}
_PATHS: Final = {key: f'/ep/{key}' for key in ENDPOINT_KEYS}


@dataclass
class _Endpoint:
    """One endpoint of the IdP: what it answers, how often it was hit, and optional gates."""

    status: int = 200
    body: Any = None
    raw: bytes | None = None
    content_type: str = 'application/json'
    hits: int = 0
    forms: list[dict[str, str]] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    started: asyncio.Event = field(default_factory=asyncio.Event)
    # When set, the handler waits for it (a never-set gate makes the endpoint hang until the fixture ends).
    gate: asyncio.Event | None = None

    async def handle(self, request: web.Request) -> web.Response:
        self.hits += 1
        self.hosts.append(request.host)

        if request.method == 'POST':
            self.forms.append({k: str(v) for k, v in (await request.post()).items()})

        self.started.set()

        if self.gate is not None:
            await self.gate.wait()

        if self.raw is not None:
            return web.Response(status=self.status, body=self.raw, content_type=self.content_type)

        return web.json_response(self.body, status=self.status)


@dataclass
class _Idp:
    url: HttpUrl
    server: TestServer
    discovery: _Endpoint
    jwks: _Endpoint
    token: _Endpoint

    @staticmethod
    def hold(endpoint: _Endpoint) -> asyncio.Event:
        endpoint.gate = asyncio.Event()

        return endpoint.gate


@pytest_asyncio.fixture
async def idp() -> AsyncIterator[_Idp]:
    app = web.Application()
    discovery = _Endpoint()
    jwks = _Endpoint(body=_KEYS_V1)
    token = _Endpoint(body=_TOKEN_BODY)

    async def config(request: web.Request) -> web.Response:
        discovery.body = {
            'issuer': f'{request.scheme}://{request.host}',
            **{key: f'{request.scheme}://{request.host}{path}' for key, path in _PATHS.items()},
        }

        return await discovery.handle(request)

    app.router.add_get('/.well-known/openid-configuration', config)
    app.router.add_get(_PATHS['jwks_uri'], jwks.handle)
    app.router.add_post(_PATHS['token_endpoint'], token.handle)

    server = TestServer(app)
    await server.start_server()
    state = _Idp(HttpUrl(str(server.make_url('')).rstrip('/')), server, discovery, jwks, token)

    try:
        yield state
    finally:
        # Release every hanging handler first, or closing the server waits for them.
        for endpoint in (discovery, jwks, token):
            if endpoint.gate is not None:
                endpoint.gate.set()

        await server.close()


@pytest_asyncio.fixture
async def make_client() -> AsyncIterator[Callable[..., OIDCClient]]:
    clients: list[OIDCClient] = []

    def make(url: HttpUrl, **kwargs: Any) -> OIDCClient:
        options: dict[str, Any] = {'timeout': 2.0, **kwargs}
        client = OIDCClient(url, 'jean', 'shh', **options)
        clients.append(client)

        return client

    yield make

    for client in clients:
        await client.aclose()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(mocker: MockerFixture) -> _Clock:
    fake = _Clock()
    mocker.patch('python3_commons.auth.monotonic', fake)

    return fake


async def _wait(event: asyncio.Event) -> None:
    async with asyncio.timeout(_WAIT_SECONDS):
        await event.wait()


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))

        return sock.getsockname()[1]


# --- error types of discovery and JWKS (pinned by tests/contract/test_handler_contract.py, A2) ---------------------

_STATUS_ERRORS: Final = [
    (400, ValueError),
    (401, PermissionError),
    (403, PermissionError),
    (404, LookupError),
    (429, InterruptedError),
    (500, aiohttp.ClientResponseError),
    (503, aiohttp.ClientResponseError),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(('status', 'exc_type'), _STATUS_ERRORS)
@pytest.mark.parametrize('endpoint', ['discovery', 'jwks'])
async def test_discovery_and_jwks_http_errors_keep_their_types(
    idp: _Idp,
    make_client: Callable[..., OIDCClient],
    endpoint: str,
    status: int,
    exc_type: type[BaseException],
) -> None:
    getattr(idp, endpoint).status = status
    getattr(idp, endpoint).body = {'error': 'x'}

    with pytest.raises(exc_type) as raised:
        await make_client(idp.url).get_jwks()

    assert type(raised.value) is exc_type


@pytest.mark.asyncio
async def test_discovery_non_json_body_is_a_value_error(idp: _Idp, make_client: Callable[..., OIDCClient]) -> None:
    idp.discovery.raw = b'<html>not json</html>'

    with pytest.raises(ValueError, match=r'.') as raised:
        await make_client(idp.url).get_config()

    assert not isinstance(raised.value, OIDCError)


@pytest.mark.asyncio
async def test_discovery_json_with_wrong_content_type_is_a_client_response_error(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    idp.discovery.raw = b'{"jwks_uri": "x"}'
    idp.discovery.content_type = 'text/html'

    with pytest.raises(aiohttp.ContentTypeError):
        await make_client(idp.url).get_config()


@pytest.mark.asyncio
async def test_discovery_without_jwks_uri_is_a_key_error(idp: _Idp, make_client: Callable[..., OIDCClient]) -> None:
    idp.discovery.raw = b'{"issuer": "x"}'
    client = make_client(idp.url)

    with pytest.raises(KeyError, match='jwks_uri'):
        await client.get_jwks()

    assert idp.jwks.hits == 0


@pytest.mark.asyncio
async def test_discovery_without_token_endpoint_is_a_key_error(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    idp.discovery.raw = b'{"issuer": "x"}'

    with pytest.raises(KeyError, match='token_endpoint'):
        await make_client(idp.url).fetch_token(username='u', password='p')

    assert idp.token.hits == 0


@pytest.mark.asyncio
async def test_incomplete_discovery_document_fails_get_provider_metadata_with_oidc_error(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    idp.discovery.raw = b'{"issuer": "x"}'

    with pytest.raises(OIDCError, match='Invalid OIDC discovery document'):
        await make_client(idp.url).get_provider_metadata()


@pytest.mark.asyncio
async def test_jwks_non_json_body_is_a_value_error(idp: _Idp, make_client: Callable[..., OIDCClient]) -> None:
    idp.jwks.raw = b'nope'

    with pytest.raises(ValueError, match=r'.'):
        await make_client(idp.url).get_jwks()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['config', 'jwks', 'token'])
async def test_connection_refused_is_connection_refused_error(
    make_client: Callable[..., OIDCClient], operation: str
) -> None:
    client = make_client(HttpUrl(f'http://127.0.0.1:{_unused_port()}'))

    calls = {
        'config': client.get_config,
        'jwks': client.get_jwks,
        'token': lambda: client.fetch_token(username='u', password='p'),
    }

    with pytest.raises(ConnectionRefusedError):
        await calls[operation]()


# --- token endpoint error mapping ---------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status', 'body', 'exc_type', 'message'),
    [
        (
            400,
            {'error': 'invalid_grant', 'error_description': 'bad password'},
            OIDCAuthError,
            'invalid_grant: bad password',
        ),
        (401, {'error': 'invalid_client', 'error_description': 'who'}, OIDCAuthError, 'invalid_client: who'),
        (400, {'error': 'unsupported_grant_type', 'error_description': 'no'}, OIDCError, 'unsupported_grant_type: no'),
        (400, {'code': 'C1', 'message': 'from message'}, OIDCError, 'C1: from message'),
        (500, {'error': 'server_error'}, OIDCError, 'server_error: '),
        (503, {}, OIDCError, 'http_503: '),
        (500, ['not', 'a', 'dict'], OIDCError, 'http_500: None'),
    ],
)
async def test_token_error_responses_map_to_oidc_errors(  # noqa: PLR0913, PLR0917
    idp: _Idp,
    make_client: Callable[..., OIDCClient],
    status: int,
    body: object,
    exc_type: type[OIDCError],
    message: str,
) -> None:
    idp.token.status = status
    idp.token.body = body

    with pytest.raises(exc_type) as raised:
        await make_client(idp.url).fetch_token(username='u', password='p')

    assert type(raised.value) is exc_type
    assert str(raised.value) == message


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [200, 502])
async def test_token_non_json_body_is_an_oidc_error_with_a_snippet(
    idp: _Idp, make_client: Callable[..., OIDCClient], status: int
) -> None:
    idp.token.status = status
    idp.token.raw = b'<html>gateway</html>'

    with pytest.raises(OIDCError, match=r'Non-JSON response from OIDC provider: .*gateway') as raised:
        await make_client(idp.url).fetch_token(username='u', password='p')

    assert type(raised.value) is OIDCError


@pytest.mark.asyncio
async def test_token_success_without_required_fields_is_a_validation_error(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    idp.token.body = {'token_type': 'Bearer'}

    with pytest.raises(msgspec.ValidationError, match='access_token'):
        await make_client(idp.url).fetch_token(username='u', password='p')


@pytest.mark.asyncio
async def test_token_post_carries_the_client_credentials(idp: _Idp, make_client: Callable[..., OIDCClient]) -> None:
    response = await make_client(idp.url).fetch_token(username='u', password='p')

    assert response.access_token == 'at'
    assert idp.token.forms == [
        {
            'grant_type': 'password',
            'username': 'u',
            'password': 'p',
            'scope': 'openid profile email',
            'client_id': 'jean',
            'client_secret': 'shh',
        }
    ]


# --- timeouts -----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['config', 'jwks', 'token'])
async def test_hanging_endpoints_hit_the_client_timeout(
    idp: _Idp, make_client: Callable[..., OIDCClient], operation: str
) -> None:
    """The discovery document hangs for 'config', the JWKS or token endpoint for the others."""
    gated = idp.discovery if operation == 'config' else idp.jwks if operation == 'jwks' else idp.token
    idp.hold(gated)
    client = make_client(idp.url, timeout=0.1)

    if operation == 'token':
        with pytest.raises(OIDCError, match='timed out') as raised:
            await client.fetch_token(username='u', password='p')

        assert isinstance(raised.value.__cause__, TimeoutError)
    else:
        with pytest.raises(TimeoutError):
            await (client.get_config() if operation == 'config' else client.get_jwks())

    assert gated.started.is_set()


@pytest.mark.asyncio
async def test_a_timed_out_client_recovers_once_the_idp_answers(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    gate = idp.hold(idp.token)
    client = make_client(idp.url, timeout=0.1)

    with pytest.raises(OIDCError, match='timed out'):
        await client.fetch_token(username='u', password='p')

    gate.set()

    assert (await client.fetch_token(username='u', password='p')).access_token == 'at'


# --- internal host rewrite ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_discovered_endpoint_is_rewritten_and_requests_use_the_internal_host(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    """The authority is unreachable; discovery, JWKS and token requests must all reach the internal IdP."""
    advertised = 'https://public.invalid'
    client = make_client(HttpUrl(advertised), authority_internal_host=idp.url)
    external = {'issuer': advertised, **{key: f'{advertised}{path}' for key, path in _PATHS.items()}}
    idp.discovery.raw = msgspec.json.encode(external)

    config = await client.get_config()
    internal = str(idp.url).rstrip('/')

    for key, path in _PATHS.items():
        assert config[key] == f'{internal}{path}'

    assert config['issuer'] == advertised
    assert await client.get_jwks() == _KEYS_V1
    assert (await client.fetch_token(username='u', password='p')).access_token == 'at'
    assert (idp.discovery.hits, idp.jwks.hits, idp.token.hits) == (1, 1, 1)
    assert idp.token.hosts == [f'{idp.server.host}:{idp.server.port}']


# --- concurrency and the shared client (1.N1) -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_async_with_users_and_an_inflight_fetch_token_share_one_session(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    client = make_client(idp.url)
    release = idp.hold(idp.token)

    async def call() -> str:
        async with client:
            return (await client.fetch_token(username='u', password='p')).access_token

    async def churn() -> None:
        for _ in range(3):
            async with client:
                await asyncio.sleep(0)

    inflight = [asyncio.create_task(call()) for _ in range(5)]
    await _wait(idp.token.started)
    session = client._session  # noqa: SLF001

    assert session is not None

    await asyncio.gather(*(churn() for _ in range(4)))

    assert not session.closed
    assert client._session is session  # noqa: SLF001

    release.set()

    assert await asyncio.gather(*inflight) == ['at'] * 5
    assert session.closed
    assert idp.token.hits == 5
    assert idp.discovery.hits == 1


@pytest.mark.asyncio
async def test_concurrent_first_calls_share_one_discovery_request(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    client = make_client(idp.url)
    release = idp.hold(idp.discovery)
    metadata_task = asyncio.create_task(client.get_provider_metadata())
    other_tasks = [
        asyncio.create_task(coro)
        for coro in (client.get_config(), client.get_jwks(), client.fetch_token(username='u', password='p'))
    ]
    await _wait(idp.discovery.started)
    await _settle()
    release.set()
    await asyncio.gather(*other_tasks)

    assert idp.discovery.hits == 1
    assert idp.jwks.hits == 1
    assert (await metadata_task).jwks_uri.endswith(_PATHS['jwks_uri'])


@pytest.mark.asyncio
async def test_sequential_async_with_rebuilds_the_session_per_use(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    client = make_client(idp.url)
    sessions = []

    for _ in range(2):
        async with client:
            await client.get_jwks()
            sessions.append(client._session)  # noqa: SLF001

    assert sessions[0] is not sessions[1]
    assert all(s is not None and s.closed for s in sessions)


# --- single-flight refresh, rate limit, no stale keys (1.N2, A8) ---------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_forced_refreshes_cause_one_jwks_request(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=1000, min_refresh_interval=0)
    await client.get_jwks()

    assert idp.jwks.hits == 1

    idp.jwks.body = _KEYS_V2
    release = idp.hold(idp.jwks)
    idp.jwks.started.clear()
    tasks = [asyncio.create_task(client.get_jwks(force_refresh=True)) for _ in range(10)]
    await _wait(idp.jwks.started)
    await _settle()
    release.set()

    assert await asyncio.gather(*tasks) == [_KEYS_V2] * 10
    assert idp.jwks.hits == 2
    assert await client.get_jwks() == _KEYS_V2


@pytest.mark.asyncio
async def test_concurrent_cold_start_callers_cause_one_jwks_request(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    client = make_client(idp.url)
    release = idp.hold(idp.jwks)
    tasks = [asyncio.create_task(client.get_jwks()) for _ in range(10)]
    await _wait(idp.jwks.started)
    await _settle()
    release.set()

    assert await asyncio.gather(*tasks) == [_KEYS_V1] * 10
    assert idp.jwks.hits == 1


@pytest.mark.asyncio
async def test_a_cancelled_caller_does_not_cancel_the_shared_refresh(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    client = make_client(idp.url)
    release = idp.hold(idp.jwks)
    first = asyncio.create_task(client.get_jwks())
    second = asyncio.create_task(client.get_jwks())
    await _wait(idp.jwks.started)
    await _settle()
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    release.set()

    assert await second == _KEYS_V1
    assert idp.jwks.hits == 1


@pytest.mark.asyncio
async def test_force_refresh_is_rate_limited_by_min_refresh_interval(
    idp: _Idp, make_client: Callable[..., OIDCClient], clock: _Clock
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=1000, min_refresh_interval=60)
    await client.get_jwks()
    idp.jwks.body = _KEYS_V2

    clock.advance(59)

    assert await client.get_jwks(force_refresh=True) == _KEYS_V1
    assert idp.jwks.hits == 1

    clock.advance(1)

    assert await client.get_jwks(force_refresh=True) == _KEYS_V2
    assert idp.jwks.hits == 2

    # The limit restarts from the attempt that just succeeded.
    idp.jwks.body = _KEYS_V1

    assert await client.get_jwks(force_refresh=True) == _KEYS_V2
    assert idp.jwks.hits == 2


@pytest.mark.asyncio
async def test_signing_key_lookup_refreshes_once_per_interval_for_an_unknown_kid(
    idp: _Idp, make_client: Callable[..., OIDCClient], clock: _Clock
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=1000, min_refresh_interval=60)
    await client.get_jwks()
    clock.advance(61)
    idp.jwks.body = _KEYS_V2

    assert await client.get_signing_key('key-2') == _KEYS_V2['keys'][0]
    assert idp.jwks.hits == 2
    assert await client.get_signing_key('unknown') is None
    assert idp.jwks.hits == 2


@pytest.mark.asyncio
async def test_fresh_keys_are_served_while_the_idp_is_down(
    idp: _Idp, make_client: Callable[..., OIDCClient], clock: _Clock
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=100)
    await client.get_jwks()
    idp.jwks.status = 500
    clock.advance(99)

    assert await client.get_jwks() == _KEYS_V1
    assert idp.jwks.hits == 1


@pytest.mark.asyncio
async def test_expired_keys_are_never_served_and_the_client_backs_off_then_recovers(
    idp: _Idp, make_client: Callable[..., OIDCClient], clock: _Clock
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=100, min_refresh_interval=60)
    await client.get_jwks()
    idp.jwks.status = 500
    clock.advance(101)

    with pytest.raises(aiohttp.ClientResponseError):
        await client.get_jwks()

    assert idp.jwks.hits == 2

    # Inside the backoff the IdP is not contacted and the original failure is the cause.
    with pytest.raises(ConnectionRefusedError, match='OIDC provider unavailable') as raised:
        await client.get_jwks()

    assert isinstance(raised.value.__cause__, aiohttp.ClientResponseError)
    assert idp.jwks.hits == 2

    with pytest.raises(ConnectionRefusedError):
        await client.get_signing_key('key-1')

    assert idp.jwks.hits == 2

    idp.jwks.status = 200
    idp.jwks.body = _KEYS_V2
    clock.advance(1)

    assert await client.get_jwks() == _KEYS_V2
    assert idp.jwks.hits == 3


@pytest.mark.asyncio
async def test_expired_keys_are_never_served_when_the_idp_is_unreachable(
    idp: _Idp, make_client: Callable[..., OIDCClient], clock: _Clock
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=100)
    await client.get_jwks()
    await idp.server.close()
    clock.advance(101)

    with pytest.raises(ConnectionRefusedError):
        await client.get_jwks()


@pytest.mark.asyncio
async def test_expired_keys_with_a_hanging_idp_time_out_instead_of_serving_stale_keys(
    idp: _Idp, make_client: Callable[..., OIDCClient], clock: _Clock
) -> None:
    client = make_client(idp.url, jwks_cache_ttl=100, timeout=0.1)
    await client.get_jwks()
    idp.hold(idp.jwks)
    clock.advance(101)

    with pytest.raises(TimeoutError):
        await client.get_jwks()


@pytest.mark.asyncio
async def test_key_set_with_an_invalid_document_is_an_oidc_error(
    idp: _Idp, make_client: Callable[..., OIDCClient]
) -> None:
    idp.jwks.body = {'keys': [{'kid': 'no-kty'}]}

    with pytest.raises(OIDCError, match='Invalid JWKS document'):
        await make_client(idp.url).get_key_set()
