import asyncio
from typing import TYPE_CHECKING

import pytest
from pydantic import HttpUrl

from python3_commons.auth import OIDCClient

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

_CONFIG = {'jwks_uri': 'https://issuer.example/keys'}
_JWKS_V1 = {'keys': [{'kid': 'key-1'}]}
_JWKS_V2 = {'keys': [{'kid': 'key-2'}]}


@pytest.fixture
def client() -> OIDCClient:
    return OIDCClient(HttpUrl('https://issuer.example'), 'jean', jwks_cache_ttl=100.0)


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
async def test_get_jwks_force_refresh_bypasses_fresh_cache(client: OIDCClient, mocker: MockerFixture) -> None:
    mocker.patch.object(client, '_fetch_config', mocker.AsyncMock(return_value=_CONFIG))
    fetch_jwks = mocker.AsyncMock(side_effect=[_JWKS_V1, _JWKS_V2])
    mocker.patch.object(client, '_fetch_jwks', fetch_jwks)
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

    async def slow_fetch(_uri: str) -> dict:
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
async def test_get_session_recreates_a_closed_session(client: OIDCClient) -> None:
    """A cache refresh after the context manager closed the session must get a live one."""
    async with client:
        first = client._get_session()  # noqa: SLF001

    assert first.closed

    async with client:
        second = client._get_session()  # noqa: SLF001

        assert not second.closed
        assert second is not first
