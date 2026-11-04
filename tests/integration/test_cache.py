import asyncio
import warnings
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import msgspec
import pytest
import pytest_asyncio
from pydantic import RedisDsn
from valkey.asyncio import BlockingConnectionPool
from valkey.asyncio.client import Pipeline
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import ResponseError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from python3_commons import cache
from python3_commons.conf import ValkeySettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import BaseModel
    from valkey.asyncio.connection import AbstractConnection

    from tests.conftest import SampleStruct

pytestmark = [pytest.mark.usefixtures('valkey_cache')]


@pytest.mark.asyncio
async def test_store_and_get_msgspec_struct(msgspec_struct: SampleStruct) -> None:
    await cache.store('test:msgspec', msgspec_struct, 8 * 3600)
    retrieved = await cache.get('test:msgspec', data_type=type(msgspec_struct))
    assert retrieved == msgspec_struct


@pytest.mark.asyncio
async def test_store_and_get_pydantic_struct(pydantic_struct: BaseModel) -> None:
    await cache.store('test:pydantic', pydantic_struct, ttl=3600)
    retrieved = await cache.get('test:pydantic', data_type=type(pydantic_struct))
    assert retrieved == pydantic_struct


@pytest.mark.asyncio
async def test_store_and_get_dict(data_dict: dict[str, Any]) -> None:
    await cache.store('test:dict', data_dict, ttl=3600)
    retrieved = await cache.get('test:dict')
    assert retrieved['A'] == data_dict['A']
    assert retrieved['B'] == data_dict['B']
    assert retrieved['C'] is None
    assert retrieved['D'] == data_dict['D'].isoformat()
    assert retrieved['E'] == data_dict['E'].isoformat()
    assert retrieved['F'] == str(data_dict['F'])


@pytest.mark.asyncio
async def test_bytes_storage() -> None:
    key = 'test:bytes'
    await cache.store_bytes(key, b'binary_data', ttl=60)
    retrieved = await cache.get_bytes(key)
    assert retrieved == b'binary_data'

    # With if_not_set=True on existing key, should not overwrite
    second_store = await cache.store_bytes(key, b'new_binary_data', ttl=60, if_not_set=True)
    assert not second_store
    assert await cache.get_bytes(key) == b'binary_data'


@pytest.mark.asyncio
async def test_string_storage() -> None:
    key = 'test:string'
    await cache.store_string(key, 'hello world', ttl=60)
    assert await cache.get_string(key) == 'hello world'
    assert await cache.get_string('test:nonexistent') is None


@pytest.mark.asyncio
async def test_sequence_storage() -> None:
    key = 'test:sequence'
    items = ['item_1', 'item_2', 'item_3']
    await cache.store_sequence(key, items, ttl=60)
    retrieved = await cache.get_sequence(key)
    assert retrieved == items


@pytest.mark.asyncio
async def test_dict_hash_storage() -> None:
    key = 'test:hash_dict'
    data = {'field1': 'value1', 'field2': 42}
    await cache.store_dict(key, data, ttl=60)
    retrieved = await cache.get_dict(key)
    assert retrieved == data


@pytest.mark.asyncio
async def test_dict_item_operations() -> None:
    key = 'test:hash_items'
    await cache.set_dict_item(key, 'foo', 'bar')
    await cache.set_dict_item(key, 'num', 100)

    assert await cache.get_dict_item(key, 'foo') == 'bar'
    assert await cache.get_dict_item(key, 'num') == 100
    assert await cache.get_dict_item(key, 'missing', default='fallback') == 'fallback'

    await cache.delete_dict_item(key, 'foo')
    assert await cache.get_dict_item(key, 'foo') is None
    assert await cache.get_dict_item(key, 'num') == 100


@pytest.mark.asyncio
async def test_set_storage() -> None:
    key = 'test:set'
    items = {'alpha', 'beta', 'gamma'}
    await cache.store_set(key, items, ttl=60)
    retrieved = await cache.get_set_members(key)
    assert retrieved == items
    assert await cache.has_set_item(key, 'alpha')
    assert not await cache.has_set_item(key, 'delta')

    await cache.add_set_item(key, 'delta')
    assert await cache.has_set_item(key, 'delta')

    await cache.delete_set_item(key, 'alpha')
    assert not await cache.has_set_item(key, 'alpha')


@pytest.mark.asyncio
async def test_delete_and_scan() -> None:
    await cache.store_string('scan_test:1', 'v1')
    await cache.store_string('scan_test:2', 'v2')

    _cursor, keys = await cache.scan(match='scan_test:*')
    decoded_keys = [k.decode('utf-8') if isinstance(k, bytes) else k for k in keys]
    assert 'scan_test:1' in decoded_keys
    assert 'scan_test:2' in decoded_keys

    await cache.delete('scan_test:1', 'scan_test:2')
    assert await cache.get_string('scan_test:1') is None
    assert await cache.get_string('scan_test:2') is None


@pytest.mark.filterwarnings('error::ResourceWarning')
def test_two_consecutive_loops_each_close_their_client() -> None:
    async def job(tag: str) -> str | None:
        await cache.store_string(f'loops:{tag}', tag, 60)
        result = await cache.get_string(f'loops:{tag}')
        await cache.close_client()

        return result

    assert asyncio.run(job('one')) == 'one'
    assert asyncio.run(job('two')) == 'two'
    assert cache._clients == {}  # noqa: SLF001


@pytest.fixture(autouse=True)
def _reset_replace_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, '_replace_warned', False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('key', 'write'),
    [
        ('g1:seq', lambda: cache.store_sequence('g1:seq', [1], ttl=60)),
        ('g1:dict', lambda: cache.store_dict('g1:dict', {'x': 1}, ttl=60)),
        ('g1:set', lambda: cache.store_set('g1:set', {'x'}, ttl=60)),
    ],
    ids=['sequence', 'dict', 'set'],
)
async def test_failed_expire_never_leaves_a_key_without_ttl(key, write) -> None:
    client = cache.get_valkey_client()
    await client.delete(key)

    with patch.object(Pipeline, 'expire', side_effect=ValkeyTimeoutError('t/o')):
        await write()

    assert await client.exists(key) == 0


@pytest.mark.asyncio
async def test_collection_writes_set_a_ttl_atomically() -> None:
    client = cache.get_valkey_client()
    await client.delete('g1:ttl')
    await cache.store_sequence('g1:ttl', ['a'], ttl=60)

    assert 0 < await client.ttl('g1:ttl') <= 60


@pytest.mark.asyncio
async def test_collection_ttl_zero_warns_and_sets_no_expiry() -> None:
    client = cache.get_valkey_client()
    await client.delete('g1:zero')

    with pytest.warns(DeprecationWarning, match='1.0'):
        await cache.store_sequence('g1:zero', [1], ttl=0)

    assert await client.ttl('g1:zero') == -1


@pytest.mark.asyncio
@pytest.mark.parametrize('ttl', [0, -1])
async def test_store_bytes_non_positive_ttl_warns_and_server_rejects_it(ttl) -> None:
    with pytest.warns(DeprecationWarning, match='1.0'), pytest.raises(ResponseError):
        await cache.store_bytes('g1:bad_ttl', b'x', ttl)


@pytest.mark.asyncio
async def test_append_sequence_and_update_dict_merge_without_warning() -> None:
    await cache.delete('g1:append', 'g1:update')

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        await cache.append_sequence('g1:append', 'a', 'b', ttl=60)
        await cache.append_sequence('g1:append', 'c', ttl=60)
        await cache.update_dict('g1:update', {'a': 1, 'b': 2}, ttl=60)
        await cache.update_dict('g1:update', {'c': 3}, ttl=60)

    assert await cache.get_sequence('g1:append') == ['a', 'b', 'c']
    assert await cache.get_dict('g1:update') == {'a': 1, 'b': 2, 'c': 3}


@pytest.mark.asyncio
async def test_restore_of_existing_key_warns_once_and_still_merges() -> None:
    await cache.delete('g1:merge')

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        await cache.store_sequence('g1:merge', ['a', 'b'], ttl=60)

    with pytest.warns(DeprecationWarning, match='replaces instead of merging'):
        await cache.store_sequence('g1:merge', ['c'], ttl=60)

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        await cache.store_sequence('g1:merge', ['d'], ttl=60)

    assert await cache.get_sequence('g1:merge') == ['a', 'b', 'c', 'd']


@pytest.mark.asyncio
async def test_decode_error_is_a_miss(caplog) -> None:
    await cache.store('g1:decode', {'x': 'not an int'}, ttl=60)

    class Point(msgspec.Struct):
        x: int

    assert await cache.get('g1:decode', default='miss', data_type=Point) == 'miss'
    assert 'could not be decoded' in caplog.text


# ---------------------------------------------------------------------------------------------------------------
# ValkeyCache (2.G2): the 1.0 contract against a real server


@pytest.fixture
def server_dsn(_valkey_dsn: RedisDsn) -> RedisDsn:
    return _valkey_dsn


@pytest_asyncio.fixture
async def vcache(server_dsn: RedisDsn) -> AsyncIterator[cache.ValkeyCache]:
    async with cache.ValkeyCache.from_settings(ValkeySettings(dsn=server_dsn)) as vcache:
        yield vcache


@pytest.mark.asyncio
async def test_valkeycache_round_trips_every_type_and_returns_bools(vcache: cache.ValkeyCache) -> None:
    await vcache.delete('v2:bytes', 'v2:str', 'v2:obj', 'v2:seq', 'v2:dict', 'v2:set', 'v2:item', 'v2:member')

    assert await vcache.store_bytes('v2:bytes', b'x', 60) is True
    assert await vcache.get_bytes('v2:bytes') == b'x'
    assert await vcache.store_bytes('v2:bytes', b'y', 60, if_not_set=True) is False
    assert await vcache.get_bytes('v2:bytes') == b'x'
    assert await vcache.store_string('v2:str', '', 60) is True
    assert await vcache.get_string('v2:str') == ''
    assert await vcache.store('v2:obj', {'a': [1, 2]}) is True
    assert await vcache.get('v2:obj') == {'a': [1, 2]}
    assert await vcache.store_sequence('v2:seq', ['a', 'b'], 60) is True
    assert await vcache.get_sequence('v2:seq') == ['a', 'b']
    assert await vcache.get_sequence('v2:seq', container=tuple) == ('a', 'b')
    assert await vcache.store_dict('v2:dict', {'f': 1, 'g': 'h'}, 60) is True
    assert await vcache.get_dict('v2:dict') == {'f': 1, 'g': 'h'}
    assert await vcache.set_dict_item('v2:item', 'k', 5) is True
    assert await vcache.get_dict_item('v2:item', 'k') == 5
    assert await vcache.delete_dict_item('v2:item', 'k', 'missing') == 1
    assert await vcache.store_set('v2:set', {'a', 'b'}, 60) is True
    assert await vcache.get_set_members('v2:set') == {'a', 'b'}
    assert await vcache.add_set_item('v2:member', 'a', 'b') is True
    assert await vcache.has_set_item('v2:member', 'a') is True
    assert await vcache.delete_set_item('v2:member', 'a') == 1
    assert await vcache.delete_set_item('v2:member', 'a') == 0
    assert await vcache.exists('v2:bytes') is True
    assert await vcache.delete('v2:bytes', 'v2:str', 'v2:nonexistent') == 2
    assert await vcache.exists('v2:bytes') is False

    cursor, found = await vcache.scan(match='v2:s*')
    keys = set(found)

    while cursor:
        cursor, found = await vcache.scan(cursor, match='v2:s*')
        keys.update(found)

    assert {b'v2:seq', b'v2:set'} <= keys

    await vcache.delete('v2:obj', 'v2:seq', 'v2:dict', 'v2:set', 'v2:item', 'v2:member')


@pytest.mark.asyncio
async def test_valkeycache_collection_stores_replace(vcache: cache.ValkeyCache) -> None:
    await vcache.delete('v2:replace:seq', 'v2:replace:dict', 'v2:replace:set')

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        await vcache.store_sequence('v2:replace:seq', ['a', 'b'], 60)
        await vcache.store_sequence('v2:replace:seq', ['c'], 60)
        await vcache.store_dict('v2:replace:dict', {'a': 1, 'b': 2}, 60)
        await vcache.store_dict('v2:replace:dict', {'c': 3}, 60)
        await vcache.store_set('v2:replace:set', {'a', 'b'}, 60)
        await vcache.store_set('v2:replace:set', {'c'}, 60)

    assert await vcache.get_sequence('v2:replace:seq') == ['c']
    assert await vcache.get_dict('v2:replace:dict') == {'c': 3}
    assert await vcache.get_set_members('v2:replace:set') == {'c'}


@pytest.mark.asyncio
async def test_valkeycache_replace_drops_the_old_ttl(vcache: cache.ValkeyCache) -> None:
    await vcache.store_sequence('v2:ttl', ['a'], 60)
    assert 0 < await vcache.client.ttl('v2:ttl') <= 60

    await vcache.store_sequence('v2:ttl', ['b'])
    assert await vcache.client.ttl('v2:ttl') == -1

    await vcache.delete('v2:ttl')


@pytest.mark.asyncio
async def test_valkeycache_storing_an_empty_collection_deletes_the_key(vcache: cache.ValkeyCache) -> None:
    await vcache.store_sequence('v2:empty:seq', ['a'], 60)
    await vcache.store_dict('v2:empty:dict', {'a': 1}, 60)
    await vcache.store_set('v2:empty:set', {'a'}, 60)

    assert await vcache.store_sequence('v2:empty:seq', []) is True
    assert await vcache.store_dict('v2:empty:dict', {}) is True
    assert await vcache.store_set('v2:empty:set', set()) is True

    assert await vcache.delete('v2:empty:seq', 'v2:empty:dict', 'v2:empty:set') == 0


@pytest.mark.asyncio
async def test_valkeycache_append_and_update_merge(vcache: cache.ValkeyCache) -> None:
    await vcache.delete('v2:merge:seq', 'v2:merge:dict')

    assert await vcache.append_sequence('v2:merge:seq', 'a', 'b', ttl=60) is True
    assert await vcache.append_sequence('v2:merge:seq', 'c', ttl=60) is True
    assert await vcache.update_dict('v2:merge:dict', {'a': 1}, ttl=60) is True
    assert await vcache.update_dict('v2:merge:dict', {'b': 2}, ttl=60) is True

    assert await vcache.get_sequence('v2:merge:seq') == ['a', 'b', 'c']
    assert await vcache.get_dict('v2:merge:dict') == {'a': 1, 'b': 2}

    await vcache.delete('v2:merge:seq', 'v2:merge:dict')


@pytest.mark.asyncio
@pytest.mark.parametrize('ttl', [0, -1])
async def test_valkeycache_non_positive_ttl_raises_value_error_and_leaves_the_key(
    ttl, vcache: cache.ValkeyCache
) -> None:
    await vcache.store_bytes('v2:bad_ttl', b'keep', 60)
    await vcache.store_sequence('v2:bad_ttl_seq', ['keep'], 60)

    with pytest.raises(ValueError, match='ttl'):
        await vcache.store_bytes('v2:bad_ttl', b'x', ttl)

    with pytest.raises(ValueError, match='ttl'):
        await vcache.store_sequence('v2:bad_ttl_seq', ['x'], ttl)

    assert await vcache.get_bytes('v2:bad_ttl') == b'keep'
    assert await vcache.get_sequence('v2:bad_ttl_seq') == ['keep']

    await vcache.delete('v2:bad_ttl', 'v2:bad_ttl_seq')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('key', 'write'),
    [
        ('v2:expire:seq', lambda c: c.store_sequence('v2:expire:seq', [1], ttl=60)),
        ('v2:expire:dict', lambda c: c.store_dict('v2:expire:dict', {'x': 1}, ttl=60)),
        ('v2:expire:set', lambda c: c.store_set('v2:expire:set', {'x'}, ttl=60)),
    ],
    ids=['sequence', 'dict', 'set'],
)
async def test_valkeycache_failed_expire_neither_replaces_nor_leaves_a_key_without_ttl(
    key, write, vcache: cache.ValkeyCache
) -> None:
    await vcache.delete(key)

    with patch.object(Pipeline, 'expire', side_effect=ValkeyTimeoutError('t/o')):
        assert await write(vcache) is False

    assert await vcache.exists(key) is False


@pytest.mark.asyncio
async def test_valkeycache_failed_replace_keeps_the_previous_value(vcache: cache.ValkeyCache) -> None:
    await vcache.store_sequence('v2:keep', ['old'], 60)

    with patch.object(Pipeline, 'expire', side_effect=ValkeyTimeoutError('t/o')):
        assert await vcache.store_sequence('v2:keep', ['new'], ttl=60) is False

    assert await vcache.get_sequence('v2:keep') == ['old']

    await vcache.delete('v2:keep')


@pytest.mark.asyncio
async def test_valkeycache_fail_open_against_a_server_that_is_down(caplog) -> None:
    settings = ValkeySettings(dsn=RedisDsn('redis://127.0.0.1:1'), retries=0, socket_connect_timeout=1)

    async with cache.ValkeyCache.from_settings(settings) as open_cache:
        assert await open_cache.get_bytes('k') is None
        assert await open_cache.store_bytes('k', b'v') is False
        assert await open_cache.delete('k') == 0

    assert caplog.text.count('Cache operation') == 1

    async with cache.ValkeyCache.from_settings(settings, fail_open=False) as closed_cache:
        with pytest.raises(ValkeyConnectionError):
            await closed_cache.get_bytes('k')


@pytest.mark.asyncio
async def test_valkeycache_bounded_pool_serves_more_tasks_than_connections(server_dsn: RedisDsn) -> None:
    settings = ValkeySettings(dsn=server_dsn, max_connections=2, pool_timeout=5)

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        assert isinstance(vcache.client.connection_pool, BlockingConnectionPool)

        results = await asyncio.gather(*(vcache.store_string(f'v2:pool:{i}', str(i), 60) for i in range(20)))

        assert results == [True] * 20
        assert await vcache.delete(*(f'v2:pool:{i}' for i in range(20))) == 20


@pytest.mark.asyncio
async def test_valkeycache_aclose_closes_the_connections_and_is_idempotent(server_dsn: RedisDsn) -> None:
    vcache = cache.ValkeyCache.from_settings(ValkeySettings(dsn=server_dsn))
    pool = vcache.client.connection_pool

    await vcache.store_string('v2:close', 'x', 60)
    connections = cast('list[AbstractConnection]', list(pool._available_connections))  # noqa: SLF001
    assert connections
    assert all(conn.is_connected for conn in connections)

    await vcache.aclose()
    await vcache.aclose()

    assert not any(conn.is_connected for conn in connections)
    assert not pool._in_use_connections  # noqa: SLF001

    # the client reconnects when it is used again, e.g. after a worker restart of the pool
    assert await vcache.get_string('v2:close') == 'x'
    await vcache.delete('v2:close')
    await vcache.aclose()


@pytest.mark.filterwarnings('error::ResourceWarning')
def test_valkeycache_two_consecutive_loops_leave_nothing_unclosed(server_dsn: RedisDsn) -> None:
    async def job(tag: str) -> str | None:
        async with cache.ValkeyCache.from_settings(ValkeySettings(dsn=server_dsn)) as vcache:
            await vcache.store_string(f'v2:loops:{tag}', tag, 60)

            return await vcache.get_string(f'v2:loops:{tag}')

    assert asyncio.run(job('one')) == 'one'
    assert asyncio.run(job('two')) == 'two'


@pytest.mark.filterwarnings('error::ResourceWarning')
def test_default_cache_per_loop_against_a_real_server(server_dsn: RedisDsn, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('VALKEY_DSN', str(server_dsn))
    monkeypatch.setattr(cache, '_default_caches', {})

    async def job(tag: str) -> str | None:
        default = cache.get_default_cache()
        await default.store_string(f'v2:default:{tag}', tag, 60)

        try:
            return await default.get_string(f'v2:default:{tag}')
        finally:
            await cache.close_default_cache()

    assert asyncio.run(job('one')) == 'one'
    assert asyncio.run(job('two')) == 'two'
    assert cache._default_caches == {}  # noqa: SLF001
