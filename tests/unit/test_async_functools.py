import asyncio
import contextvars
import gc
import inspect
import logging
from functools import _make_key as std_make_key
from typing import TypeVar

import pytest

from python3_commons import async_functools
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

    # Let every caller join the in-flight computation
    await asyncio.sleep(0)
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


async def _drain():
    """Wait until every other task on the running loop has finished"""
    current = asyncio.current_task()

    if pending := {task for task in asyncio.all_tasks() if task is not current}:
        await asyncio.wait(pending)


@pytest.mark.asyncio
@pytest.mark.parametrize('maxsize', [2, None])
async def test_async_lru_cache_cancelled_first_caller_does_not_cancel_others(maxsize):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    @async_lru_cache(maxsize=maxsize)
    async def slow_work(x: int) -> int:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

        return x

    # Task 1 starts and waits inside slow_work
    task1 = asyncio.create_task(slow_work(42))
    await started.wait()

    # Task 2 also requests key 42 while in flight
    task2 = asyncio.create_task(slow_work(42))
    await asyncio.sleep(0)

    task1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task1

    # The computation outlives its first caller and task 2 still gets the result
    release.set()
    assert await task2 == 42
    assert calls == 1

    # The result was cached and the next call does not run the function again
    assert await slow_work(42) == 42
    assert calls == 1
    assert slow_work.cache_info().hits == 1


@pytest.mark.asyncio
async def test_async_lru_cache_cancelled_waiter_does_not_affect_owner_or_others():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> int:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

        return x

    owner = asyncio.create_task(work(1))
    await started.wait()
    waiter = asyncio.create_task(work(1))
    bystander = asyncio.create_task(work(1))
    await asyncio.sleep(0)

    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    release.set()
    assert await owner == 1
    assert await bystander == 1
    assert calls == 1
    assert work.cache_info() == (0, 1, 8, 1)


@pytest.mark.asyncio
async def test_async_lru_cache_timeout_of_one_caller_does_not_cancel_patient_caller():
    started = asyncio.Event()
    release = asyncio.Event()
    timeouts: list[asyncio.Timeout] = []
    calls = 0

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> int:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

        return x

    async def impatient() -> str:
        try:
            async with asyncio.timeout(None) as timeout:
                timeouts.append(timeout)

                return str(await work(1))
        except TimeoutError:
            return 'timeout'

    short = asyncio.create_task(impatient())
    await started.wait()
    patient = asyncio.create_task(work(1))
    await asyncio.sleep(0)

    # Expire the first caller's timeout now, without waiting for a timer
    timeouts[0].reschedule(asyncio.get_running_loop().time())
    assert await short == 'timeout'
    assert not patient.done()

    release.set()
    assert await patient == 1
    assert patient.cancelling() == 0
    assert calls == 1


@pytest.mark.asyncio
async def test_async_lru_cache_taskgroup_keeps_result_when_first_caller_times_out():
    started = asyncio.Event()
    release = asyncio.Event()
    timeouts: list[asyncio.Timeout] = []

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> int:
        started.set()
        await release.wait()

        return x

    async def impatient() -> str:
        try:
            async with asyncio.timeout(None) as timeout:
                timeouts.append(timeout)

                return str(await work(1))
        except TimeoutError:
            return 'timeout'

    async with asyncio.TaskGroup() as tg:
        short = tg.create_task(impatient())
        patient = tg.create_task(work(1))
        await started.wait()

        timeouts[0].reschedule(asyncio.get_running_loop().time())
        assert await short == 'timeout'

        release.set()

    assert patient.result() == 1


@pytest.mark.asyncio
async def test_async_lru_cache_sole_caller_cancelled_cancels_work_and_clears_in_flight():
    started = asyncio.Event()
    work_cancelled = asyncio.Event()
    calls = 0

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> int:
        nonlocal calls
        calls += 1

        if calls == 1:
            started.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                work_cancelled.set()

                raise

        return x

    caller = asyncio.create_task(work(1))
    await started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert work._in_flight == {}  # noqa: SLF001
    await work_cancelled.wait()
    await _drain()
    assert work.cache_info().currsize == 0

    # Nothing stale is left behind: the next call runs the function again
    assert await work(1) == 1
    assert calls == 2


@pytest.mark.asyncio
async def test_async_lru_cache_late_joiner_does_not_inherit_cancellation():
    started = asyncio.Event()
    unwinding = asyncio.Event()
    finish_unwind = asyncio.Event()
    calls = 0

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> str:
        nonlocal calls
        calls += 1

        if calls == 1:
            started.set()

            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                unwinding.set()
                await finish_unwind.wait()

                raise

        return f'call {calls}'

    first = asyncio.create_task(work(1))
    await started.wait()
    first.cancel()

    # The first computation is cancelled but still cleaning up when the new caller arrives
    await unwinding.wait()
    late = asyncio.create_task(work(1))
    await asyncio.sleep(0)
    finish_unwind.set()

    with pytest.raises(asyncio.CancelledError):
        await first

    assert await late == 'call 2'
    await _drain()
    assert work.cache_info().currsize == 1


@pytest.mark.asyncio
async def test_async_lru_cache_base_exception_reaches_every_caller():
    class Abort(BaseException):
        pass

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> int:
        nonlocal calls
        calls += 1

        if calls == 1:
            started.set()
            await release.wait()

            raise Abort

        return x

    owner = asyncio.create_task(work(1))
    await started.wait()
    waiter = asyncio.create_task(work(1))
    await asyncio.sleep(0)
    release.set()

    # The timeout is only a safety net against a hang
    async with asyncio.timeout(1):
        with pytest.raises(Abort):
            await owner

        with pytest.raises(Abort):
            await waiter

    assert work._in_flight == {}  # noqa: SLF001
    assert await work(1) == 1
    assert calls == 2


@pytest.mark.asyncio
async def test_async_lru_cache_logs_no_asyncio_errors(caplog):
    started = asyncio.Event()
    release = asyncio.Event()

    @async_lru_cache(maxsize=8)
    async def failing(x: int) -> int:
        raise ValueError(x)

    @async_lru_cache(maxsize=8)
    async def slow(x: int) -> int:
        started.set()
        await release.wait()

        return x

    @async_lru_cache(maxsize=8)
    async def slow_failing(x: int) -> int:
        await release.wait()

        raise ValueError(x)

    with caplog.at_level(logging.ERROR, logger='asyncio'):
        # a failing call that nobody else waits for
        with pytest.raises(ValueError, match='1'):
            await failing(1)

        # a cancelled waiter next to a successful owner
        owner = asyncio.create_task(slow(1))
        await started.wait()
        waiter = asyncio.create_task(slow(1))
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()
        assert await owner == 1
        await asyncio.gather(waiter, return_exceptions=True)

        # a cancelled waiter next to an owner whose work fails
        release.clear()
        owner = asyncio.create_task(slow_failing(2))
        waiter = asyncio.create_task(slow_failing(2))
        await asyncio.sleep(0)
        waiter.cancel()
        release.set()
        with pytest.raises(ValueError, match='2'):
            await owner

        await asyncio.gather(waiter, return_exceptions=True)
        await _drain()
        del owner, waiter
        gc.collect()

    assert [record for record in caplog.records if record.name == 'asyncio' and record.levelno >= logging.ERROR] == []


@pytest.mark.asyncio
async def test_async_lru_cache_context_variable_set_in_cached_function_does_not_reach_caller():
    var = contextvars.ContextVar('var', default='unset')

    @async_lru_cache(maxsize=8)
    async def load(x: int) -> int:
        var.set('set-inside-cached-fn')

        return x

    async def caller() -> str:
        await load(1)

        return var.get()

    assert await asyncio.create_task(caller()) == 'unset'
    assert var.get() == 'unset'


@pytest.mark.asyncio
async def test_async_lru_cache_result_is_computed_in_first_callers_context():
    var = contextvars.ContextVar('var', default='unset')
    started = asyncio.Event()
    release = asyncio.Event()

    @async_lru_cache(maxsize=8)
    async def load(x: int) -> str:
        started.set()
        await release.wait()

        return var.get()

    async def caller(value: str) -> str:
        var.set(value)

        return await load(1)

    first = asyncio.create_task(caller('first'))
    await started.wait()
    second = asyncio.create_task(caller('second'))
    await asyncio.sleep(0)
    release.set()

    assert await first == 'first'
    assert await second == 'first'
    assert await asyncio.create_task(caller('third')) == 'first'


@pytest.mark.asyncio
async def test_async_lru_cache_eager_task_factory_leaves_no_stale_in_flight_entry():
    loop = asyncio.get_running_loop()
    calls = 0

    @async_lru_cache(maxsize=8)
    async def failing(x: int) -> int:
        nonlocal calls
        calls += 1

        raise ValueError(x)

    loop.set_task_factory(asyncio.eager_task_factory)

    try:
        # the task finishes inside create_task, before the cache could register it
        with pytest.raises(ValueError, match='1'):
            await failing(1)

        assert failing._in_flight == {}  # noqa: SLF001

        with pytest.raises(ValueError, match='1'):
            await failing(1)
    finally:
        loop.set_task_factory(None)

    assert calls == 2


def test_async_lru_cache_shared_across_event_loops():
    calls = 0
    gates: dict[asyncio.AbstractEventLoop, asyncio.Future[str]] = {}
    started: dict[asyncio.AbstractEventLoop, asyncio.Future[None]] = {}

    @async_lru_cache(maxsize=8)
    async def work(x: int) -> str:
        nonlocal calls
        calls += 1
        loop = asyncio.get_running_loop()
        started[loop].set_result(None)

        return await gates[loop]

    def prepare(loop: asyncio.AbstractEventLoop) -> None:
        gates[loop] = loop.create_future()
        started[loop] = loop.create_future()

    async def second_loop() -> str:
        loop = asyncio.get_running_loop()
        prepare(loop)
        task = asyncio.create_task(work(1))
        # A failing call finishes without ever starting the function
        await asyncio.wait({task, started[loop]}, return_when=asyncio.FIRST_COMPLETED)

        if started[loop].done():
            gates[loop].set_result('B')

        return await task

    async def third_loop() -> str:
        return await work(1)

    loop1 = asyncio.new_event_loop()

    try:
        prepare(loop1)
        first = loop1.create_task(work(1))
        loop1.run_until_complete(started[loop1])

        # The first loop is suspended inside the cached function, a second loop calls it with the same key
        assert asyncio.run(second_loop()) == 'B'
        assert calls == 2

        # Finishing the first loop does not replace the cached value
        gates[loop1].set_result('A')
        assert loop1.run_until_complete(first) == 'A'
        assert work._in_flight == {}  # noqa: SLF001
    finally:
        loop1.run_until_complete(loop1.shutdown_asyncgens())
        loop1.close()

    # Cached values are shared by every loop
    assert asyncio.run(third_loop()) == 'B'
    assert calls == 2
    assert work.cache_info().hits == 1


@pytest.mark.parametrize(
    ('old_name', 'new_name'),
    [
        ('LRUAsyncCallable', 'AsyncLRUCache'),
        ('LRUAsyncBoundCallable', 'BoundAsyncLRUCache'),
        ('BaseLRUAsyncCallable', 'AsyncLRUCache'),
    ],
)
def test_old_class_names_are_deprecated_aliases(old_name, new_name):
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        old = getattr(async_functools, old_name)

    assert old is getattr(async_functools, new_name)
    assert record[0].filename == __file__


def test_old_concrete_class_names_resolve_to_the_implementations():
    @async_lru_cache(maxsize=2)
    async def cached(x):
        return x

    @async_lru_cache(maxsize=0)
    async def uncached(x):
        return x

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert isinstance(cached, async_functools.CachedLRUAsyncCallable)

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert isinstance(uncached, async_functools.UncachedLRUAsyncCallable)


@pytest.mark.parametrize('name', ['AC', 'AC_invariant'])
def test_old_typevars_are_deprecated(name):
    with pytest.warns(DeprecationWarning, match='1.0'):
        assert isinstance(getattr(async_functools, name), TypeVar)


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError, match='no_such_name'):
        _ = async_functools.no_such_name


def test_new_names_do_not_warn():
    assert {'AsyncLRUCache', 'BoundAsyncLRUCache'} <= set(async_functools.__all__)
    # the deprecated alias stays in __all__ until 1.0, so a star import keeps binding it (and warns)
    assert 'LRUAsyncCallable' in async_functools.__all__


@pytest.mark.asyncio
async def test_bound_method_is_a_coroutine_function_and_keys_on_self():
    class A:
        @async_lru_cache
        async def meth(self, x):
            return x

    a = A()

    assert inspect.iscoroutinefunction(a.meth.__call__)
    assert isinstance(a.meth, async_functools.BoundAsyncLRUCache)
    assert isinstance(A.meth, async_functools.AsyncLRUCache)
    assert await a.meth(1) == 1
    assert A.meth.cache_info().currsize == 1


@pytest.mark.parametrize('typed', [False, True])
def test_vendored_make_key_matches_functools(typed):
    cases = [((), {}), ((1,), {}), (('a',), {}), ((1.5,), {}), ((1, 'b'), {'k': 2, 'j': 3}), ((), {'x': 1})]

    def normalized(key):
        # each module has its own keyword marker, a bare object()
        return tuple('<kwd_mark>' if type(item) is object else item for item in key) if isinstance(key, tuple) else key

    for args, kwargs in cases:
        ours = async_functools._make_key(args, kwargs, typed=typed)  # noqa: SLF001

        assert normalized(ours) == normalized(std_make_key(args, kwargs, typed=typed))
