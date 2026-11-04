# pyright: strict
import asyncio
import functools
import inspect
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, overload

__all__ = ['log_execution_time', 'retry']

_RetryOn = type[BaseException] | tuple[type[BaseException], ...]


class _RetryDecorator(Protocol):
    @overload
    def __call__[**P, R](self, func: Callable[P, Awaitable[R]], /) -> Callable[P, Awaitable[R]]: ...
    @overload
    def __call__[**P, R](self, func: Callable[P, R], /) -> Callable[P, R]: ...


@overload
def log_execution_time[**P, R](func: Callable[P, Awaitable[R]], /) -> Callable[P, Awaitable[R]]: ...
@overload
def log_execution_time[**P, R](func: Callable[P, R], /) -> Callable[P, R]: ...
@overload
def log_execution_time(
    func: None = None, /, *, level: int = ...
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...
def log_execution_time(func: Callable[..., Any] | None = None, /, *, level: int = logging.INFO) -> Any:
    """Log how long the decorated sync or async function took. Usable as ``@log_execution_time`` or with ``level=``."""

    def decorate(fn: Any) -> Callable[..., Any]:
        _logger = logging.getLogger(fn.__module__)
        name = f'{fn.__module__}.{fn.__qualname__}'

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start_time = time.perf_counter()

                try:
                    return await fn(*args, **kwargs)
                finally:
                    _logger.log(level, '%s executed in %.4f seconds', name, time.perf_counter() - start_time)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.perf_counter()

            try:
                return fn(*args, **kwargs)
            finally:
                _logger.log(level, '%s executed in %.4f seconds', name, time.perf_counter() - start_time)

        return sync_wrapper

    if func is None:
        return decorate

    return decorate(func)


def _next_delay(attempt: int, delay: float, backoff: float, max_delay: float, *, jitter: bool) -> float:
    value = min(delay * backoff ** (attempt - 1), max_delay)

    return random.uniform(0, value) if jitter else value  # noqa: S311


def retry(  # noqa: C901, PLR0913  # public signature is pinned (C1/C2)
    *,
    attempts: int = 3,
    retry_on: _RetryOn = (ConnectionError, TimeoutError),
    delay: float = 0.1,
    backoff: float = 2.0,
    max_delay: float = 5.0,
    jitter: bool = True,
) -> _RetryDecorator:
    """Retry the decorated async (or sync) function on ``retry_on`` exceptions with exponential backoff.

    Each failed attempt is logged at WARNING; the last exception is re-raised after ``attempts`` tries.
    ``asyncio.CancelledError`` is never retried. A 429 from api_client is the builtin ``InterruptedError``,
    so add that class to ``retry_on`` to retry it.
    """
    if attempts < 1:
        msg = 'attempts must be >= 1'
        raise ValueError(msg)

    def decorate(fn: Any) -> Callable[..., Any]:  # noqa: C901
        _logger = logging.getLogger(fn.__module__)
        name = f'{fn.__module__}.{fn.__qualname__}'

        def _failed(attempt: int, exc: BaseException) -> float | None:
            if attempt >= attempts:
                _logger.warning('%s failed on attempt %d/%d, giving up: %r', name, attempt, attempts, exc)

                return None

            wait = _next_delay(attempt, delay, backoff, max_delay, jitter=jitter)
            _logger.warning('%s failed on attempt %d/%d: %r; retrying in %.3fs', name, attempt, attempts, exc, wait)

            return wait

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                attempt = 0

                while True:
                    attempt += 1

                    try:
                        return await fn(*args, **kwargs)
                    except asyncio.CancelledError:
                        raise
                    except retry_on as exc:
                        if (wait := _failed(attempt, exc)) is None:
                            raise

                        await asyncio.sleep(wait)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0

            while True:
                attempt += 1

                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:
                    if (wait := _failed(attempt, exc)) is None:
                        raise

                    time.sleep(wait)

        return sync_wrapper

    return decorate
