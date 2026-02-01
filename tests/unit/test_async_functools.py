import asyncio

import pytest

from python3_commons.async_functools import async_lru_cache


@pytest.mark.asyncio
async def test_async_lru_cache_basic():
    call_count = 0

    @async_lru_cache(maxsize=2)
    async def fast_func(x):
        nonlocal call_count
        call_count += 1
        return x

    assert await fast_func(1) == 1
    assert await fast_func(1) == 1
    assert call_count == 1


@pytest.mark.asyncio
async def test_async_lru_cache_maxsize():
    call_count = 0

    @async_lru_cache(maxsize=2)
    async def func(x):
        nonlocal call_count
        call_count += 1
        return x

    await func(1)
    await func(2)
    await func(3)  # Evicts 1

    call_count = 0
    await func(2)
    assert call_count == 0
    await func(3)
    assert call_count == 0
    await func(1)
    assert call_count == 1


@pytest.mark.asyncio
async def test_async_lru_cache_concurrency():
    call_count = 0
    start_event = asyncio.Event()

    @async_lru_cache(maxsize=2)
    async def slow_func(x):
        nonlocal call_count
        await start_event.wait()
        call_count += 1

        return x

    # Launch multiple concurrent calls for same key
    tasks = [asyncio.create_task(slow_func(1)) for _ in range(5)]

    # Let them all reach the wait
    await asyncio.sleep(0.1)
    start_event.set()

    results = await asyncio.gather(*tasks)
    assert all(r == 1 for r in results)
    assert call_count == 1


@pytest.mark.asyncio
async def test_async_lru_cache_exception():
    call_count = 0
    msg = 'error'

    @async_lru_cache(maxsize=2)
    async def error_func(x):
        nonlocal call_count
        call_count += 1

        raise ValueError(msg)

    with pytest.raises(ValueError, match=msg):
        await error_func(1)

    # Try again, should not be cached (or should it? standard lru_cache caches results,
    # but usually you don't want to cache exceptions forever if they might be transient.
    # However, the current implementation sets the exception on the future and deletes from in_flight.
    # It doesn't add it to 'cache'.)

    with pytest.raises(ValueError, match=msg):
        await error_func(1)

    assert call_count == 2


@pytest.mark.asyncio
async def test_async_lru_cache_info():
    @async_lru_cache(maxsize=10)
    async def func(x):
        return x

    await func(1)
    await func(1)
    await func(2)

    info = func.cache_info()
    assert info.hits == 1
    assert info.misses == 2
    assert info.currsize == 2
    assert info.maxsize == 10

    params = func.cache_parameters()
    assert params['maxsize'] == 10
    assert params['typed'] is False

    func.cache_discard(1)
    info = func.cache_info()
    assert info.currsize == 1

    func.cache_clear()
    info = func.cache_info()
    assert info.hits == 0
    assert info.misses == 0
    assert info.currsize == 0


@pytest.mark.asyncio
async def test_async_lru_cache_method():
    class A:
        def __init__(self):
            self.count = 0

        @async_lru_cache(maxsize=2)
        async def meth(self, x):
            self.count += 1

            return x + self.count

    a = A()
    assert await a.meth(1) == 2
    assert await a.meth(1) == 2
    assert a.count == 1

    assert await a.meth(2) == 4
    assert a.count == 2

    # Check that cache is instance-specific if bound,
    # but wait, standard lru_cache on method is shared across instances if not careful.
    # Actually, lru_cache on a method creates ONE cache for that method,
    # and 'self' is part of the key.
    b = A()
    assert await b.meth(1) == 2
    assert b.count == 1

    info = A.meth.cache_info()
    assert info.currsize == 2  # (a, 2), (b, 1) - (a, 1) was evicted because maxsize=2

    A.meth.cache_clear()
    assert A.meth.cache_info().currsize == 0


@pytest.mark.asyncio
async def test_async_lru_cache_decorator_no_args():
    @async_lru_cache
    async def func(x):
        return x

    assert await func(1) == 1
    assert func.cache_info().maxsize == 128


@pytest.mark.asyncio
async def test_async_lru_cache_none_maxsize():
    @async_lru_cache(maxsize=None)
    async def func(x):
        return x

    for i in range(200):
        await func(i)

    info = func.cache_info()
    assert info.currsize == 200
    assert info.maxsize is None


@pytest.mark.asyncio
async def test_async_lru_cache_maxsize_zero():
    call_count = 0

    @async_lru_cache(maxsize=0)
    async def func(x):
        nonlocal call_count
        call_count += 1
        return x

    assert await func(1) == 1
    assert await func(1) == 1
    assert call_count == 2

    info = func.cache_info()
    assert info.currsize == 0
    assert info.misses == 2


@pytest.mark.asyncio
async def test_async_lru_cache_protocol():
    @async_lru_cache()
    async def func(x: int) -> int:
        return x

    # This is more for type checking, but we can call them
    assert func.cache_info().maxsize == 128
    func.cache_clear()
