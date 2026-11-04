"""Type-level checks of python3_commons.cache; checked with ty and pyright, never collected by pytest."""

from collections.abc import Sequence
from typing import Any, assert_type

from python3_commons import cache
from python3_commons.cache import ValkeyCache


class Point:
    x: int


async def module_functions(default: int) -> None:
    assert_type(await cache.scan(), tuple[int, list[bytes]] | tuple[int, tuple[()]])
    assert_type(await cache.exists('k'), bool)
    assert_type(await cache.get_bytes('k'), bytes | None)
    assert_type(await cache.store_bytes('k', b'v'), bool | None)
    assert_type(await cache.get_string('k'), str | None)
    assert_type(await cache.get('k'), Any)
    assert_type(await cache.get('k', data_type=Point), Point | None)
    assert_type(await cache.get('k', default, Point), Point | int)
    assert_type(await cache.get_dict_item('k', 'f', Point), Point | None)
    assert_type(await cache.get_dict_item('k', 'f', Point, default), Point | int)
    assert_type(await cache.get_set_members('k'), set[Any] | None)


async def valkey_cache(c: ValkeyCache, default: int) -> None:
    assert_type(await c.get_bytes('k'), bytes | None)
    assert_type(await c.store_bytes('k', b'v'), bool)
    assert_type(await c.delete('k'), int)
    assert_type(await c.scan(), tuple[int, Sequence[bytes]])
    assert_type(await c.get('k', data_type=Point), Point | None)
    assert_type(await c.get('k', default, Point), Point | int)
    assert_type(await c.get_dict_item('k', 'f', Point), Point | None)
