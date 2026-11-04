import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
import pytest_asyncio
from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.test_utils import TestServer
from pydantic import HttpUrl, SecretStr

from python3_commons.auth import OIDCAuthError, OIDCClient, OIDCTokenResponse, TokenData
from python3_commons.conf import OIDCSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from pytest_mock import MockerFixture

_CONFIG = {'jwks_uri': 'https://issuer.example/keys'}
_JWKS_V1 = {'keys': [{'kid': 'key-1'}]}
_JWKS_V2 = {'keys': [{'kid': 'key-2'}]}


def _make_client(mocker: MockerFixture, fetch_jwks: Any, **kwargs: Any) -> OIDCClient:
    """A client whose discovery and JWKS fetches are replaced, so no session is ever built."""
    options: dict[str, Any] = {'jwks_cache_ttl': 100.0, **kwargs}
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean', **options)
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    mocker.patch.object(client, '_fetch_jwks', fetch_jwks)

    return client


@pytest_asyncio.fixture
async def client() -> AsyncIterator[OIDCClient]:
    async with OIDCClient(HttpUrl('https://issuer.example'), 'jean', jwks_cache_ttl=100.0) as oidc_client:
        yield oidc_client


@pytest.mark.asyncio
async def test_get_jwks_serves_cached_set_within_ttl(client: OIDCClient, mocker: MockerFixture) -> None:
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    fetch_jwks = mocker.AsyncMock(return_value=_JWKS_V1)
    mocker.patch.object(client, '_fetch_jwks', fetch_jwks)
    mocker.patch('python3_commons.auth.monotonic', return_value=10.0)

    first = await client.get_jwks()
    second = await client.get_jwks()

    assert first == _JWKS_V1
    assert second == _JWKS_V1
    assert fetch_jwks.await_count == 1


@pytest.mark.asyncio
async def test_get_jwks_refetches_after_ttl_expiry(client: OIDCClient, mocker: MockerFixture) -> None:
    """A rotated signing key is picked up once the cached set goes stale."""
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, _JWKS_V2])
    mocker.patch.object(client, '_fetch_jwks', fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    first = await client.get_jwks()
    clock.return_value = 100.0
    second = await client.get_jwks()

    assert first == _JWKS_V1
    assert second == _JWKS_V2
    assert fetch_jwks.await_count == 2


@pytest.mark.asyncio
async def test_get_jwks_force_refresh_bypasses_fresh_cache(mocker: MockerFixture) -> None:
    """With the rate limit off (``min_refresh_interval=0``) a forced refresh always fetches."""
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, _JWKS_V2])
    client = _make_client(mocker, fetch_jwks, min_refresh_interval=0.0)
    mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    first = await client.get_jwks()
    second = await client.get_jwks(force_refresh=True)

    assert first == _JWKS_V1
    assert second == _JWKS_V2
    assert fetch_jwks.await_count == 2


@pytest.mark.asyncio
async def test_get_jwks_coalesces_concurrent_refreshes(client: OIDCClient, mocker: MockerFixture) -> None:
    """Concurrent first-time callers share a single fetch and never deadlock on the lock."""
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(_uri: str) -> dict[str, Any]:
        started.set()
        await release.wait()

        return _JWKS_V1

    fetch_jwks = mocker.AsyncMock(side_effect=slow_fetch)
    mocker.patch.object(client, '_fetch_jwks', fetch_jwks)

    first = asyncio.create_task(client.get_jwks())
    await started.wait()
    second = asyncio.create_task(client.get_jwks())
    await asyncio.sleep(0)
    release.set()

    results = await asyncio.gather(first, second)

    assert results == [_JWKS_V1, _JWKS_V1]
    assert fetch_jwks.await_count == 1


@pytest.mark.asyncio
async def test_fetch_token_success(client: OIDCClient, mocker: MockerFixture) -> None:
    config = {'token_endpoint': 'https://issuer.example/oauth2/token'}
    mocker.patch.object(client, 'get_config', mocker.AsyncMock(return_value=config))

    mock_resp = mocker.MagicMock()
    mock_resp.status = 200
    mock_resp.read = mocker.AsyncMock(
        return_value=b'{"access_token": "token123", "expires_in": 3600, "id_token": "id123", "token_type": "Bearer"}'
    )

    mock_ctx = mocker.AsyncMock()
    mock_ctx.__aenter__.return_value = mock_resp
    mock_ctx.__aexit__.return_value = None

    mocker.patch('python3_commons.api_client.request', return_value=mock_ctx)

    token = await client.fetch_token(username='user@example.com', password='password123')
    assert token.access_token == 'token123'
    assert token.token_type == 'Bearer'
    assert token.expires_in == 3600


@pytest.mark.asyncio
async def test_fetch_token_auth_error(client: OIDCClient, mocker: MockerFixture) -> None:
    config = {'token_endpoint': 'https://issuer.example/oauth2/token'}
    mocker.patch.object(client, 'get_config', mocker.AsyncMock(return_value=config))

    mock_resp = mocker.MagicMock()
    mock_resp.status = 400
    mock_resp.read = mocker.AsyncMock(
        return_value=b'{"error": "invalid_grant", "error_description": "Incorrect username or password."}'
    )

    mock_ctx = mocker.AsyncMock()
    mock_ctx.__aenter__.return_value = mock_resp
    mock_ctx.__aexit__.return_value = None

    mocker.patch('python3_commons.api_client.request', return_value=mock_ctx)

    with pytest.raises(OIDCAuthError, match=r'invalid_grant: Incorrect username or password\.'):
        await client.fetch_token(username='user@example.com', password='wrongpassword')


@pytest.mark.asyncio
async def test_get_jwks_is_bounded_by_timeout() -> None:
    async def hang(_request: web.Request) -> web.Response:
        await asyncio.sleep(1.5)

        return web.json_response({})

    app = web.Application()
    app.router.add_get('/.well-known/openid-configuration', hang)

    async with TestServer(app) as server:
        url = HttpUrl(str(server.make_url('')).rstrip('/'))

        async with OIDCClient(url, 'jean', timeout=0.2) as oidc_client:
            # Swap in a session with a long timeout, so only asyncio.timeout can bound the call.
            await (await oidc_client._get_session()).close()  # noqa: SLF001
            oidc_client._session = ClientSession(timeout=ClientTimeout(total=30))  # noqa: SLF001
            started = time.monotonic()

            with pytest.raises(TimeoutError):
                await oidc_client.get_jwks()

            assert time.monotonic() - started < 0.6


_WAIT_SECONDS = 5.0
_TOKEN_BODY = {'access_token': 'a', 'expires_in': 1, 'id_token': 'i', 'token_type': 'Bearer'}


async def _wait(event: asyncio.Event) -> None:
    async with asyncio.timeout(_WAIT_SECONDS):
        await event.wait()


@dataclass
class _Idp:
    """A real identity provider whose key and token endpoints answer only after ``release`` is set."""

    url: HttpUrl = field(init=False)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    keys_started: asyncio.Event = field(default_factory=asyncio.Event)
    token_started: asyncio.Event = field(default_factory=asyncio.Event)


@pytest_asyncio.fixture
async def idp() -> AsyncIterator[_Idp]:
    server_state = _Idp()

    async def config(request: web.Request) -> web.Response:
        base = f'{request.scheme}://{request.host}'

        return web.json_response({'jwks_uri': f'{base}/keys', 'token_endpoint': f'{base}/token'})

    async def keys(_request: web.Request) -> web.Response:
        server_state.keys_started.set()
        await server_state.release.wait()

        return web.json_response(_JWKS_V1)

    async def token(_request: web.Request) -> web.Response:
        server_state.token_started.set()
        await server_state.release.wait()

        return web.json_response(_TOKEN_BODY)

    app = web.Application()
    app.router.add_get('/.well-known/openid-configuration', config)
    app.router.add_get('/keys', keys)
    app.router.add_post('/token', token)

    async with TestServer(app) as server:
        server_state.url = HttpUrl(str(server.make_url('')).rstrip('/'))

        yield server_state

        server_state.release.set()


async def _get_jwks_refresh(client: OIDCClient) -> dict[str, Any]:
    return await client.get_jwks(force_refresh=True)


async def _fetch_token(client: OIDCClient) -> OIDCTokenResponse:
    return await client.fetch_token(username='user', password='password')


@pytest.mark.asyncio
@pytest.mark.parametrize('use_context_manager', [True, False], ids=['async_with', 'direct'])
@pytest.mark.parametrize(
    ('call', 'started'),
    [(_get_jwks_refresh, 'keys_started'), (_fetch_token, 'token_started')],
    ids=['get_jwks_force_refresh', 'fetch_token'],
)
async def test_inflight_call_survives_another_tasks_exit(
    idp: _Idp,
    call: Callable[[OIDCClient], Awaitable[object]],
    started: str,
    use_context_manager: bool,  # noqa: FBT001
) -> None:
    """Task A leaving ``async with`` must not close the session under task B's request (auth-1)."""
    client = OIDCClient(idp.url, 'jean')

    async def request_b() -> object:
        if not use_context_manager:
            return await call(client)

        async with client:
            return await call(client)

    task_b = asyncio.create_task(request_b())

    try:
        await _wait(getattr(idp, started))

        session = client._session  # noqa: SLF001

        assert session is not None

        async with client:
            pass

        assert not session.closed

        idp.release.set()

        assert await task_b

        if use_context_manager:
            assert session.closed
    finally:
        idp.release.set()
        await asyncio.gather(task_b, return_exceptions=True)
        await client.aclose()


@pytest.mark.asyncio
async def test_session_closes_only_when_the_last_user_exits() -> None:
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean')

    async with client:
        session = await client._get_session()  # noqa: SLF001

        async with client:
            assert await client._get_session() is session  # noqa: SLF001

        assert not session.closed

    assert session.closed


@pytest.mark.asyncio
async def test_sequential_use_closes_the_session_and_reuse_builds_a_new_one() -> None:
    """Today's per-call ``async with`` behaviour: every sequential use gets and then closes its own session."""
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean')

    async with client:
        first = await client._get_session()  # noqa: SLF001

        assert not first.closed

    assert first.closed

    async with client:
        second = await client._get_session()  # noqa: SLF001

        assert not second.closed
        assert second is not first

    assert second.closed


@pytest.mark.asyncio
async def test_aclose_then_reuse_gets_a_fresh_session(idp: _Idp) -> None:
    idp.release.set()
    client = OIDCClient(idp.url, 'jean', min_refresh_interval=0.0)

    try:
        await client.get_jwks()

        first = client._session  # noqa: SLF001

        assert first is not None

        await client.aclose()
        await client.aclose()

        assert first.closed
        assert client._session is None  # noqa: SLF001

        await client.get_jwks(force_refresh=True)

        second = client._session  # noqa: SLF001

        assert second is not None
        assert second is not first
        assert not second.closed
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_aclose_closes_the_session_even_while_users_are_inside() -> None:
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean')

    async with client:
        session = await client._get_session()  # noqa: SLF001

        await client.aclose()

        assert session.closed

    assert client._session is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_user_arriving_while_the_last_exit_is_closing_gets_a_fresh_session(mocker: MockerFixture) -> None:
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean')
    closing = asyncio.Event()
    proceed = asyncio.Event()
    original_close = ClientSession.close

    async def slow_close(self: ClientSession) -> None:
        closing.set()
        await proceed.wait()
        await original_close(self)

    mocker.patch.object(ClientSession, 'close', slow_close)

    await client.__aenter__()

    first = client._session  # noqa: SLF001
    exit_task = asyncio.create_task(client.__aexit__(None, None, None))

    try:
        await _wait(closing)
        await client.__aenter__()

        second = client._session  # noqa: SLF001

        assert second is not first
        assert second is not None
        assert not second.closed
    finally:
        proceed.set()
        await exit_task
        await client.aclose()

    assert first is not None
    assert first.closed
    assert second.closed


@pytest.mark.asyncio
async def test_cancelled_call_releases_its_inflight_slot(idp: _Idp) -> None:
    """A cancelled caller frees its slot; the refresh Task keeps its own until it is done."""
    client = OIDCClient(idp.url, 'jean')

    try:
        caller = asyncio.create_task(client.get_jwks())
        await _wait(idp.keys_started)

        refresh = client._jwks_task  # noqa: SLF001

        assert refresh is not None

        caller.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await caller

        assert client._inflight == 1  # noqa: SLF001

        idp.release.set()
        await refresh

        assert client._inflight == 0  # noqa: SLF001

        async with client:
            session = await client._get_session()  # noqa: SLF001

        assert session.closed
    finally:
        idp.release.set()
        await client.aclose()


@pytest.mark.asyncio
async def test_injected_session_is_borrowed(idp: _Idp) -> None:
    idp.release.set()

    async with ClientSession() as session:
        client = OIDCClient(idp.url, 'jean', session=session)

        async with client:
            await client.get_jwks()

        await client.aclose()
        await client.aclose()

        assert not session.closed

        await client.get_jwks(force_refresh=True)

        assert client._session is session  # noqa: SLF001


def test_module_level_client_survives_consecutive_event_loops(mocker: MockerFixture) -> None:
    """A client built once and reused on a new loop (pytest function-scoped loops) rebuilds its state."""
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean', min_refresh_interval=0.0)
    gate: dict[str, asyncio.Event] = {}

    async def slow_fetch(_uri: str) -> dict[str, Any]:
        gate['started'].set()
        await gate['release'].wait()

        return _JWKS_V1

    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    mocker.patch.object(client, '_fetch_jwks', slow_fetch)
    sessions: list[ClientSession] = []

    async def run_once() -> list[dict[str, Any]]:
        gate['started'] = asyncio.Event()
        gate['release'] = asyncio.Event()

        async with client:
            sessions.append(await client._get_session())  # noqa: SLF001

            first = asyncio.create_task(client.get_jwks(force_refresh=True))
            await _wait(gate['started'])

            # The second caller runs up to the contended lock and parks there.
            second = asyncio.create_task(client.get_jwks(force_refresh=True))
            await asyncio.sleep(0)
            gate['release'].set()

            return list(await asyncio.gather(first, second))

    assert asyncio.run(run_once()) == [_JWKS_V1, _JWKS_V1]
    assert asyncio.run(run_once()) == [_JWKS_V1, _JWKS_V1]
    assert sessions[0] is not sessions[1]
    assert all(session.closed for session in sessions)


def test_open_session_from_a_closed_loop_is_dropped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean')
    sessions: list[ClientSession] = []

    async def leak() -> None:
        # Entered and never exited: the loop goes away with one user still counted.
        await client.__aenter__()
        sessions.append(await client._get_session())  # noqa: SLF001

    async def reuse() -> None:
        async with client:
            sessions.append(await client._get_session())  # noqa: SLF001

    asyncio.run(leak())

    with caplog.at_level(logging.WARNING, logger='python3_commons.auth'):
        asyncio.run(reuse())

    assert 'another event loop' in caplog.text
    assert sessions[0] is not sessions[1]
    assert sessions[1].closed
    assert not sessions[0].closed

    asyncio.run(sessions[0].close())


@pytest.mark.asyncio
async def test_client_busy_on_another_loop_raises(mocker: MockerFixture) -> None:
    client = OIDCClient(HttpUrl('https://issuer.example'), 'jean')
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    mocker.patch.object(client, '_fetch_jwks', mocker.AsyncMock(return_value=_JWKS_V1))

    async with client:
        with pytest.raises(RuntimeError, match='OIDCClient is in use on another event loop'):
            await asyncio.to_thread(asyncio.run, client.get_jwks())


# --- 1.N2: JWKS refresh policy, rate limit, signing keys and endpoint rewrite ---


@pytest.mark.asyncio
async def test_concurrent_callers_with_idp_down_cause_one_fetch(mocker: MockerFixture) -> None:
    """Five waiters share one failed fetch instead of retrying one after another (auth-8)."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def failing_fetch(_uri: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

        msg = 'down'

        raise ConnectionRefusedError(msg)

    client = _make_client(mocker, failing_fetch, jwks_cache_ttl=0.0)
    mocker.patch('python3_commons.auth.monotonic', return_value=0.0)
    callers = [asyncio.create_task(client.get_jwks()) for _ in range(5)]
    await _wait(started)
    release.set()
    results = await asyncio.gather(*callers, return_exceptions=True)

    assert calls == 1
    assert all(isinstance(result, ConnectionRefusedError) for result in results)
    assert all(str(result) == 'down' for result in results)


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_the_shared_refresh(mocker: MockerFixture) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    fetch_jwks = mocker.AsyncMock()

    async def slow_fetch(_uri: str) -> dict[str, Any]:
        started.set()
        await release.wait()

        return _JWKS_V1

    fetch_jwks.side_effect = slow_fetch
    client = _make_client(mocker, fetch_jwks)
    first = asyncio.create_task(client.get_jwks())
    second = asyncio.create_task(client.get_jwks())
    await _wait(started)
    first.cancel()

    with contextlib.suppress(asyncio.CancelledError):
        await first

    release.set()

    assert await second == _JWKS_V1
    assert fetch_jwks.await_count == 1


@pytest.mark.asyncio
async def test_refresh_keeps_its_session_after_every_caller_left(idp: _Idp) -> None:
    """The refresh Task holds an in-flight slot, so no caller leaving ``async with`` closes its session."""
    client = OIDCClient(idp.url, 'jean')

    try:
        async with client:
            caller = asyncio.create_task(client.get_jwks())
            await _wait(idp.keys_started)
            caller.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await caller

        session = client._session  # noqa: SLF001

        assert session is not None
        assert not session.closed

        idp.release.set()

        assert await client.get_jwks() == _JWKS_V1
    finally:
        idp.release.set()
        await client.aclose()


@pytest.mark.asyncio
async def test_failed_refresh_backs_off_with_unavailable_error(mocker: MockerFixture) -> None:
    """Backoff 1 s, 2 s, ... capped at ``min_refresh_interval``; inside it the IdP is not contacted (auth-8)."""
    down = ConnectionRefusedError('down')
    fetch_jwks = mocker.AsyncMock(side_effect=[down, ConnectionRefusedError('down'), _JWKS_V1])
    client = _make_client(mocker, fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    with pytest.raises(ConnectionRefusedError, match=r'^down$') as first:
        await client.get_jwks()

    clock.return_value = 0.5

    with pytest.raises(ConnectionRefusedError, match=r'^OIDC provider unavailable$') as backed_off:
        await client.get_jwks()

    assert backed_off.value.__cause__ is first.value
    assert fetch_jwks.await_count == 1

    clock.return_value = 1.5  # one second after the first failure: retry, which fails again

    with pytest.raises(ConnectionRefusedError, match=r'^down$'):
        await client.get_jwks()

    clock.return_value = 3.0  # the second failure doubled the backoff to 2 s

    with pytest.raises(ConnectionRefusedError, match=r'^OIDC provider unavailable$'):
        await client.get_jwks()

    assert fetch_jwks.await_count == 2

    clock.return_value = 3.5

    assert await client.get_jwks() == _JWKS_V1
    assert fetch_jwks.await_count == 3

    clock.return_value = 4.0

    assert await client.get_jwks() == _JWKS_V1  # a success clears the failure state


@pytest.mark.asyncio
async def test_backoff_is_capped_at_min_refresh_interval(mocker: MockerFixture) -> None:
    fetch_jwks = mocker.AsyncMock(side_effect=[ConnectionRefusedError('down')] * 4 + [_JWKS_V1])
    client = _make_client(mocker, fetch_jwks, min_refresh_interval=5.0)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    for now in (0.0, 1.0, 3.0, 7.0):  # failures 1 to 4 end after waits of 0, 1, 2 and 4 s
        clock.return_value = now

        with pytest.raises(ConnectionRefusedError, match=r'^down$'):
            await client.get_jwks()

    clock.return_value = 11.0  # the fifth backoff would be 8 s; the cap makes it 5 s

    with pytest.raises(ConnectionRefusedError, match=r'^OIDC provider unavailable$'):
        await client.get_jwks()

    clock.return_value = 12.0

    assert await client.get_jwks() == _JWKS_V1


@pytest.mark.asyncio
async def test_zero_interval_disables_backoff(mocker: MockerFixture) -> None:
    fetch_jwks = mocker.AsyncMock(side_effect=[ConnectionRefusedError('down'), ConnectionRefusedError('down')])
    client = _make_client(mocker, fetch_jwks, min_refresh_interval=0.0)
    mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    for _ in range(2):
        with pytest.raises(ConnectionRefusedError, match=r'^down$'):
            await client.get_jwks()

    assert fetch_jwks.await_count == 2


@pytest.mark.asyncio
async def test_expired_keys_are_never_served_when_the_idp_is_down(mocker: MockerFixture) -> None:
    """A8: once the TTL has passed and the refresh fails, the old keys are not returned."""
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, ConnectionRefusedError('down')])
    client = _make_client(mocker, fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    assert await client.get_jwks() == _JWKS_V1

    clock.return_value = 200.0

    with pytest.raises(ConnectionRefusedError):
        await client.get_jwks()

    clock.return_value = 200.5

    with pytest.raises(ConnectionRefusedError, match=r'^OIDC provider unavailable$'):
        await client.get_jwks()


@pytest.mark.asyncio
async def test_force_refresh_is_rate_limited(mocker: MockerFixture) -> None:
    """100 forced refreshes (an unknown kid per request) cost one IdP fetch per interval (auth-9)."""
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, _JWKS_V2])
    client = _make_client(mocker, fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    results = [await client.get_jwks(force_refresh=True) for _ in range(100)]

    assert results == [_JWKS_V1] * 100
    assert fetch_jwks.await_count == 1

    clock.return_value = 61.0

    assert await client.get_jwks(force_refresh=True) == _JWKS_V2
    assert fetch_jwks.await_count == 2


@pytest.mark.asyncio
async def test_force_refresh_during_a_running_refresh_joins_it(mocker: MockerFixture) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_fetch(_uri: str) -> dict[str, Any]:
        started.set()
        await release.wait()

        return _JWKS_V1

    fetch_jwks = mocker.AsyncMock(side_effect=slow_fetch)
    client = _make_client(mocker, fetch_jwks, min_refresh_interval=0.0)
    first = asyncio.create_task(client.get_jwks())
    await _wait(started)
    second = asyncio.create_task(client.get_jwks(force_refresh=True))
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == [_JWKS_V1, _JWKS_V1]
    assert fetch_jwks.await_count == 1


def test_negative_min_refresh_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match='min_refresh_interval'):
        OIDCClient(HttpUrl('https://issuer.example'), 'jean', min_refresh_interval=-1.0)


@pytest.mark.asyncio
async def test_get_signing_key_returns_a_known_key_without_refreshing(mocker: MockerFixture) -> None:
    fetch_jwks = mocker.AsyncMock(return_value=_JWKS_V1)
    client = _make_client(mocker, fetch_jwks)
    mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    assert await client.get_signing_key('key-1') == {'kid': 'key-1'}
    assert fetch_jwks.await_count == 1


@pytest.mark.asyncio
async def test_get_signing_key_refreshes_once_per_interval_for_an_unknown_kid(mocker: MockerFixture) -> None:
    fetch_jwks = mocker.AsyncMock(return_value=_JWKS_V1)
    client = _make_client(mocker, fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    assert await client.get_signing_key('unknown') is None  # healthy IdP: None, not an error
    assert fetch_jwks.await_count == 1  # the first load just fetched; a forced refresh would be rate-limited

    clock.return_value = 61.0

    for _ in range(10):
        assert await client.get_signing_key('unknown') is None

    assert fetch_jwks.await_count == 2  # one refresh for ten lookups

    clock.return_value = 122.0

    assert await client.get_signing_key('unknown') is None
    assert fetch_jwks.await_count == 3


@pytest.mark.asyncio
async def test_get_signing_key_finds_a_rotated_key(mocker: MockerFixture) -> None:
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, _JWKS_V2])
    client = _make_client(mocker, fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    assert await client.get_signing_key('key-1') == {'kid': 'key-1'}

    clock.return_value = 61.0

    assert await client.get_signing_key('key-2') == {'kid': 'key-2'}


@pytest.mark.asyncio
async def test_get_signing_key_propagates_unavailable_when_keys_expired(mocker: MockerFixture) -> None:
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, ConnectionRefusedError('down')])
    client = _make_client(mocker, fetch_jwks)
    clock = mocker.patch('python3_commons.auth.monotonic', return_value=0.0)

    assert await client.get_signing_key('key-1') == {'kid': 'key-1'}

    clock.return_value = 200.0

    with pytest.raises(ConnectionRefusedError):
        await client.get_signing_key('key-1')


def test_refresh_cut_off_by_loop_shutdown_does_not_block_the_next_loop(mocker: MockerFixture) -> None:
    gate: dict[str, asyncio.Event] = {}
    calls = 0

    async def fetch(_uri: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1

        if calls == 1:
            gate['started'].set()
            await asyncio.Event().wait()  # never answers: the loop shuts down first

        return _JWKS_V1

    client = _make_client(mocker, fetch)

    async def first_loop() -> None:
        gate['started'] = asyncio.Event()
        asyncio.create_task(client.get_jwks())  # noqa: RUF006  # cancelled by asyncio.run
        await _wait(gate['started'])

    async def second_loop() -> dict[str, Any]:
        return await client.get_jwks()

    asyncio.run(first_loop())

    assert client._jwks_task is None  # noqa: SLF001
    assert client._inflight == 0  # noqa: SLF001
    assert asyncio.run(second_loop()) == _JWKS_V1


_EXTERNAL = 'https://idp.invalid/realms/app'
_ENDPOINT_PATHS = {
    'token_endpoint': 'protocol/openid-connect/token',
    'jwks_uri': 'protocol/openid-connect/certs',
    'userinfo_endpoint': 'protocol/openid-connect/userinfo',
    'introspection_endpoint': 'protocol/openid-connect/token/introspect',
    'revocation_endpoint': 'protocol/openid-connect/revoke',
    'end_session_endpoint': 'protocol/openid-connect/logout',
}


@pytest.mark.asyncio
async def test_get_config_rewrites_every_endpoint_to_the_internal_host(mocker: MockerFixture) -> None:
    discovered = {'issuer': _EXTERNAL, **{key: f'{_EXTERNAL}/{path}' for key, path in _ENDPOINT_PATHS.items()}}
    del discovered['revocation_endpoint']  # absent keys are skipped, not invented
    client = OIDCClient(
        HttpUrl(_EXTERNAL),
        'jean',
        authority_internal_host=HttpUrl('http://keycloak:8080/'),
    )
    fetch_config = mocker.AsyncMock(return_value=discovered)
    mocker.patch.object(client, '_fetch_config', fetch_config)

    config = await client.get_config()

    assert config['issuer'] == _EXTERNAL
    assert 'revocation_endpoint' not in config
    assert config['token_endpoint'] == 'http://keycloak:8080/realms/app/protocol/openid-connect/token'
    assert config['jwks_uri'] == 'http://keycloak:8080/realms/app/protocol/openid-connect/certs'
    assert config['userinfo_endpoint'] == 'http://keycloak:8080/realms/app/protocol/openid-connect/userinfo'
    assert (
        config['introspection_endpoint'] == 'http://keycloak:8080/realms/app/protocol/openid-connect/token/introspect'
    )
    assert config['end_session_endpoint'] == 'http://keycloak:8080/realms/app/protocol/openid-connect/logout'
    assert await client.get_config() is config
    assert fetch_config.await_count == 1
    assert discovered['token_endpoint'].startswith(_EXTERNAL)  # the discovered document is not mutated


@pytest.mark.asyncio
async def test_get_config_without_internal_host_keeps_the_discovered_endpoints(mocker: MockerFixture) -> None:
    discovered = {key: f'{_EXTERNAL}/{path}' for key, path in _ENDPOINT_PATHS.items()}
    client = OIDCClient(HttpUrl(_EXTERNAL), 'jean')
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=discovered))

    assert await client.get_config() == discovered


@pytest.mark.asyncio
async def test_token_and_jwks_requests_go_to_the_internal_host() -> None:
    """Split-horizon: the IdP advertises its public URLs, but every request must use the internal host (auth-10)."""
    hits: list[str] = []

    async def config(_request: web.Request) -> web.Response:
        return web.json_response({key: f'{_EXTERNAL}/{path}' for key, path in _ENDPOINT_PATHS.items()})

    async def keys(_request: web.Request) -> web.Response:
        hits.append('certs')

        return web.json_response(_JWKS_V1)

    async def token(_request: web.Request) -> web.Response:
        hits.append('token')

        return web.json_response(_TOKEN_BODY)

    app = web.Application()
    app.router.add_get('/realms/app/.well-known/openid-configuration', config)
    app.router.add_get('/realms/app/protocol/openid-connect/certs', keys)
    app.router.add_post('/realms/app/protocol/openid-connect/token', token)

    async with TestServer(app) as server:
        internal = HttpUrl(str(server.make_url('')).rstrip('/'))

        async with OIDCClient(HttpUrl(_EXTERNAL), 'jean', timeout=2.0, authority_internal_host=internal) as oidc:
            assert await oidc.get_jwks() == _JWKS_V1
            assert (await oidc.fetch_token(username='user', password='password')).access_token == 'a'

    assert hits == ['certs', 'token']


def test_from_settings_maps_refresh_interval_and_cache_ttl() -> None:
    settings = OIDCSettings(
        authority_url=HttpUrl('https://issuer.example'),
        client_id='jean',
        client_secret=SecretStr('s3cret'),
        jwks_refresh_interval=7.0,
        jwks_cache_ttl=42.0,
        timeout=3,
        verify_cert=False,
        connection_limit=5,
    )
    client = OIDCClient.from_settings(settings)

    assert client._min_refresh_interval == 7.0  # noqa: SLF001
    assert client._jwks_cache_ttl == 42.0  # noqa: SLF001
    assert client._client_secret is not None  # noqa: SLF001
    assert client._client_secret.get_secret_value() == 's3cret'  # noqa: SLF001
    assert 's3cret' not in repr(vars(client))  # held as SecretStr
    assert client._timeout == 3  # noqa: SLF001
    assert client._verify_cert is False  # noqa: SLF001
    assert client._connection_limit == 5  # noqa: SLF001


@pytest.mark.asyncio
async def test_from_settings_rate_limits_force_refresh_and_expires_cache(mocker: MockerFixture) -> None:
    settings = OIDCSettings(
        authority_url=HttpUrl('https://issuer.example'),
        client_id='jean',
        jwks_refresh_interval=50.0,
        jwks_cache_ttl=100.0,
    )
    client = OIDCClient.from_settings(settings)
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, _JWKS_V2, _JWKS_V2])
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    mocker.patch.object(client, '_fetch_jwks', fetch_jwks)
    now = mocker.patch('python3_commons.auth.monotonic', return_value=1000.0)

    assert await client.get_jwks() == _JWKS_V1
    now.return_value = 1010.0
    assert await client.get_jwks(force_refresh=True) == _JWKS_V1  # inside the 50 s rate limit
    now.return_value = 1060.0
    assert await client.get_jwks(force_refresh=True) == _JWKS_V2  # past the limit, cache still fresh
    now.return_value = 1159.0
    assert await client.get_jwks() == _JWKS_V2
    assert fetch_jwks.await_count == 2
    now.return_value = 1161.0  # 101 s after the last fetch: expired
    await client.get_jwks()

    assert fetch_jwks.await_count == 3


def test_from_settings_overrides_win_over_settings() -> None:
    settings = OIDCSettings(authority_url=HttpUrl('https://issuer.example'), client_id='jean')
    client = OIDCClient.from_settings(settings, client_id='other', min_refresh_interval=1.0, audit_name='idp')

    assert client._client_id == 'other'  # noqa: SLF001
    assert client._min_refresh_interval == 1.0  # noqa: SLF001
    assert client._audit_name == 'idp'  # noqa: SLF001
    assert client._client_secret is None  # noqa: SLF001


def test_from_settings_falls_back_to_shared_settings(mocker: MockerFixture) -> None:
    shared = OIDCSettings(authority_url=HttpUrl('https://shared.example'), client_id='shared')
    mocker.patch('python3_commons.auth.get_oidc_settings', return_value=shared)

    assert OIDCClient.from_settings()._client_id == 'shared'  # noqa: SLF001


@pytest.mark.parametrize(
    'settings',
    [
        OIDCSettings(client_id='jean'),
        OIDCSettings(authority_url=HttpUrl('https://issuer.example')),
    ],
)
def test_from_settings_requires_authority_url_and_client_id(settings: OIDCSettings) -> None:
    with pytest.raises(ValueError, match='authority_url and client_id'):
        OIDCClient.from_settings(settings)


def test_token_data_new_claims_default_and_positional_construction_still_works() -> None:
    token = TokenData(2, 1, 'iss', 'sub')

    assert (token.nbf, token.azp, token.scope, token.jti, token.sid) == (None,) * 5
    assert token.groups == []


def test_token_data_decodes_new_claims_and_has_role() -> None:
    token = msgspec.convert(
        {
            'exp': 2, 'iat': 1, 'iss': 'i', 'sub': 's', 'nbf': 1, 'azp': 'app', 'scope': 'openid', 'jti': 'j',
            'sid': 'sid-1', 'groups': ['g'], 'realm_access': {'roles': ['admin']},
            'resource_access': {'app': {'roles': ['editor']}},
        },
        TokenData,
    )  # fmt: skip

    assert (token.nbf, token.azp, token.scope, token.jti, token.sid, token.groups) == (
        1,
        'app',
        'openid',
        'j',
        'sid-1',
        ['g'],
    )
    assert token.has_role('admin')
    assert token.has_role('editor')
    assert not token.has_role('g')
    assert not token.has_role('nope')
