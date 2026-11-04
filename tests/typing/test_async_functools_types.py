"""Type-checked only, never collected by pytest (see conftest.py): the typing contract of async_lru_cache.

``ty check tests/typing`` and ``pyright tests/typing`` must report 0 errors. ``assert_type`` makes both checkers
enforce the inferred types.
"""

from collections.abc import Awaitable, Callable
from typing import assert_type

from python3_commons.async_functools import AsyncLRUCache, BoundAsyncLRUCache, CacheInfo, async_lru_cache


@async_lru_cache
async def bare(x: int, y: str = 'a') -> float:
    return float(x)


@async_lru_cache()
async def called(x: int) -> str:
    return str(x)


@async_lru_cache(maxsize=None, typed=True)
async def configured(x: int, *, flag: bool = False) -> bytes:
    return b''


class Service:
    @async_lru_cache(maxsize=4)
    async def method(self, key: str, count: int = 1) -> list[str]:
        return [key] * count

    @async_lru_cache
    async def bare_method(self, key: str) -> int:
        return len(key)


async def check_functions() -> None:
    # Parameter names survive, so the exact ParamSpec cannot be spelled in assert_type; assignability to the
    # plain Callable checks parameters and result, and the calls below check names, defaults and keywords.
    as_callable: Callable[[int, str], Awaitable[float]] = bare
    as_other_callable: Callable[[int], Awaitable[str]] = called
    _ = (as_callable, as_other_callable)
    assert isinstance(bare, AsyncLRUCache)

    assert_type(await bare(1), float)
    assert_type(await bare(1, 'b'), float)
    assert_type(await called(1), str)
    assert_type(await configured(1, flag=True), bytes)

    assert_type(bare.cache_info(), CacheInfo)
    assert_type(bare.cache_info().hits, int)
    bare.cache_clear()
    bare.cache_discard(1)
    assert_type(bare.cache_parameters()['maxsize'], int | None)


async def check_methods() -> None:
    service = Service()

    bound: Callable[[str, int], Awaitable[list[str]]] = service.method
    unbound: Callable[[Service, str, int], Awaitable[list[str]]] = Service.method
    _ = (bound, unbound)
    assert isinstance(service.method, BoundAsyncLRUCache)

    assert_type(await service.method('a'), list[str])
    assert_type(await service.method('a', 2), list[str])
    assert_type(await service.bare_method('a'), int)
    assert_type(service.method.cache_info(), CacheInfo)
    service.method.cache_clear()
    service.method.cache_discard('a')
    Service.method.cache_clear()


async def check_errors() -> None:
    service = Service()

    await bare('1')  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
    await bare()  # pyright: ignore[reportCallIssue]  # ty: ignore[missing-argument]
    await bare(1, 'b', 3)  # pyright: ignore[reportCallIssue]  # ty: ignore[too-many-positional-arguments]
    await configured(1, True)  # noqa: FBT003  # pyright: ignore[reportCallIssue]  # ty: ignore[too-many-positional-arguments]
    await service.method(1)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
    await service.method('a', 1, 2)  # pyright: ignore[reportCallIssue]  # ty: ignore[too-many-positional-arguments]
    await service.bare_method('a', 'b')  # pyright: ignore[reportCallIssue]  # ty: ignore[too-many-positional-arguments]
