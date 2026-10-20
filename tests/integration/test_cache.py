from typing import TYPE_CHECKING

import pytest

from python3_commons import cache

if TYPE_CHECKING:
    from pydantic import BaseModel

    from tests.conftest import TestStruct

pytestmark = [pytest.mark.usefixtures('valkey_cache'), pytest.mark.asyncio]


async def test_store_and_get_msgspec_struct(msgspec_struct: TestStruct) -> None:
    await cache.store('test:msgspec', msgspec_struct, 8 * 3600)
    retrieved = await cache.get('test:msgspec', data_type=type(msgspec_struct))
    assert retrieved == msgspec_struct


async def test_store_and_get_pydantic_struct(pydantic_struct: BaseModel) -> None:
    await cache.store('test:pydantic', pydantic_struct, ttl=3600)
    retrieved = await cache.get('test:pydantic', data_type=type(pydantic_struct))
    assert retrieved == pydantic_struct


async def test_store_and_get_dict(data_dict: dict) -> None:
    await cache.store('test:dict', data_dict, ttl=3600)
    retrieved = await cache.get('test:dict')
    assert retrieved['A'] == data_dict['A']
    assert retrieved['B'] == data_dict['B']
    assert retrieved['C'] is None
    assert retrieved['D'] == data_dict['D'].isoformat()
    assert retrieved['E'] == data_dict['E'].isoformat()
    assert retrieved['F'] == str(data_dict['F'])


async def test_bytes_storage() -> None:
    key = 'test:bytes'
    await cache.store_bytes(key, b'binary_data', ttl=60)
    retrieved = await cache.get_bytes(key)
    assert retrieved == b'binary_data'

    # With if_not_set=True on existing key, should not overwrite
    second_store = await cache.store_bytes(key, b'new_binary_data', ttl=60, if_not_set=True)
    assert not second_store
    assert await cache.get_bytes(key) == b'binary_data'


async def test_string_storage() -> None:
    key = 'test:string'
    await cache.store_string(key, 'hello world', ttl=60)
    assert await cache.get_string(key) == 'hello world'
    assert await cache.get_string('test:nonexistent') is None


async def test_sequence_storage() -> None:
    key = 'test:sequence'
    items = ['item_1', 'item_2', 'item_3']
    await cache.store_sequence(key, items, ttl=60)
    retrieved = await cache.get_sequence(key)
    assert retrieved == items


async def test_dict_hash_storage() -> None:
    key = 'test:hash_dict'
    data = {'field1': 'value1', 'field2': 42}
    await cache.store_dict(key, data, ttl=60)
    retrieved = await cache.get_dict(key)
    assert retrieved == data


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
