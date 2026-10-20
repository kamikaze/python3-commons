from unittest.mock import AsyncMock, MagicMock

import pytest
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from python3_commons import cache


@pytest.fixture(params=[ValkeyConnectionError, ValkeyTimeoutError])
def broken_client(request, mocker):
    client = MagicMock()

    methods = 'scan delete exists set get rpush lrange expire hset hgetall hget hdel sadd sismember srem smembers'

    for method in methods.split():
        setattr(client, method, AsyncMock(side_effect=request.param('down')))

    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('call', 'expected'),
    [
        (cache.scan, (0, ())),
        (lambda: cache.delete('k'), None),
        (lambda: cache.exists('k'), False),
        (lambda: cache.store_bytes('k', b'v'), None),
        (lambda: cache.get_bytes('k'), None),
        (lambda: cache.store('k', {'a': 1}), None),
        (lambda: cache.get('k', default='fallback'), 'fallback'),
        (lambda: cache.store_string('k', 'v'), None),
        (lambda: cache.get_string('k'), None),
        (lambda: cache.store_sequence('k', [1, 2]), None),
        (lambda: cache.get_sequence('k'), []),
        (lambda: cache.get_sequence('k', tuple), ()),
        (lambda: cache.store_dict('k', {'a': 1}), None),
        (lambda: cache.get_dict('k'), None),
        (lambda: cache.get_dict_item('k', 'a', default='fallback'), 'fallback'),
        (lambda: cache.set_dict_item('k', 'a', 1), None),
        (lambda: cache.delete_dict_item('k', 'a'), None),
        (lambda: cache.store_set('k', {'a'}), None),
        (lambda: cache.has_set_item('k', 'a'), False),
        (lambda: cache.add_set_item('k', 'a'), None),
        (lambda: cache.delete_set_item('k', 'a'), None),
        (lambda: cache.get_set_members('k'), None),
    ],
)
@pytest.mark.usefixtures('broken_client')
async def test_cache_degrades_gracefully_when_valkey_is_unavailable(call, expected, caplog) -> None:
    assert await call() == expected
    assert 'Cache operation' in caplog.text


@pytest.mark.asyncio
async def test_store_dict_and_get_dict_use_the_same_serializer(mocker) -> None:
    stored = {}
    client = MagicMock()
    client.hset = AsyncMock(side_effect=lambda name, mapping: stored.update(mapping))
    client.hgetall = AsyncMock(side_effect=lambda name: {k.encode(): v for k, v in stored.items()})
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    await cache.store_dict('k', {'a': 1, 2: 'b'})

    assert await cache.get_dict('k') == {'a': 1, '2': 'b'}
