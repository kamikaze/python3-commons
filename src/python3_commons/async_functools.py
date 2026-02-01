from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Hashable
from functools import _make_key, update_wrapper
from typing import (
    Any,
    NamedTuple,
    Protocol,
    TypedDict,
    TypeVar,
    cast,
    overload,
)

AC_invariant = TypeVar('AC_invariant', bound=Callable[..., Any])
AC = TypeVar('AC', bound=Callable[..., Any])


class CacheInfo(NamedTuple):
    """
    Metadata on the current state of a cache
    """

    hits: int
    misses: int
    maxsize: int | None
    currsize: int


class CacheParameters(TypedDict):
    """
    Metadata on the parameters of a cache
    """

    maxsize: int | None
    typed: bool


class LRUAsyncCallable(Protocol[AC_invariant]):
    """
    Protocol of a LRU cache wrapping a callable to an awaitable
    """

    __wrapped__: AC_invariant

    def __get__(self, instance: object, owner: type | None = None) -> Any: ...

    __call__: AC_invariant

    def cache_parameters(self) -> CacheParameters: ...
    def cache_info(self) -> CacheInfo: ...
    def cache_clear(self) -> None: ...
    def cache_discard(self, *args: Any, **kwargs: Any) -> None: ...


class LRUAsyncBoundCallable[AC: Callable[..., Any]]:
    """A LRUAsyncCallable that is bound like a method"""

    __slots__ = ('__self__', '__weakref__', '_lru')

    def __init__(self, lru: LRUAsyncCallable[AC], __self__: object):
        self._lru = lru
        self.__self__ = __self__

    @property
    def __wrapped__(self) -> AC:
        return self._lru.__wrapped__

    @property
    def __func__(self) -> LRUAsyncCallable[AC]:
        return self._lru

    def __get__(self, instance: object, owner: type | None = None) -> LRUAsyncBoundCallable[AC]:
        return LRUAsyncBoundCallable(self._lru, instance)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._lru(self.__self__, *args, **kwargs)

    def cache_parameters(self) -> CacheParameters:
        return self._lru.cache_parameters()

    def cache_info(self) -> CacheInfo:
        return self._lru.cache_info()

    def cache_clear(self) -> None:
        return self._lru.cache_clear()

    def cache_discard(self, *args: Any, **kwargs: Any) -> None:
        return self._lru.cache_discard(self.__self__, *args, **kwargs)

    def __repr__(self) -> str:
        name = getattr(self.__wrapped__, '__qualname__', '?')

        return f'<bound async cache {name} of {self.__self__}>'

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lru, name)


@overload
def async_lru_cache[AC: Callable[..., Any]](maxsize: AC, *, typed: bool = ...) -> LRUAsyncCallable[AC]: ...


@overload
def async_lru_cache[AC: Callable[..., Any]](
    maxsize: int | None = ..., *, typed: bool = ...
) -> Callable[[AC], LRUAsyncCallable[AC]]: ...


def async_lru_cache[AC: Callable[..., Any]](
    maxsize: int | AC | None = 128, *, typed: bool = False
) -> LRUAsyncCallable[AC] | Callable[[AC], LRUAsyncCallable[AC]]:
    """
    Least Recently Used cache for async functions
    """
    if isinstance(maxsize, int):
        maxsize = max(maxsize, 0)
    elif maxsize is None:
        pass
    elif callable(maxsize):
        # used as function decorator, first arg is the function to be wrapped
        func = cast('AC', maxsize)
        wrapper = CachedLRUAsyncCallable(func, typed=typed, maxsize=128)
        update_wrapper(cast('Any', wrapper), func)

        return cast('LRUAsyncCallable[AC]', wrapper)
    else:
        # This branch handles cases where maxsize is not int, None, or callable.
        # It also helps avoid "unreachable code" warnings in some IDEs.
        msg = f"first argument to 'async_lru_cache' must be an int, a callable or None, not {type(maxsize).__name__}"
        raise TypeError(msg)

    def lru_decorator(function: AC) -> LRUAsyncCallable[AC]:
        wrapper: MemoizedLRUAsyncCallable[AC] | UncachedLRUAsyncCallable[AC] | CachedLRUAsyncCallable[AC]

        if maxsize is None:
            wrapper = MemoizedLRUAsyncCallable(function, typed=typed)
        elif maxsize == 0:
            wrapper = UncachedLRUAsyncCallable(function, typed=typed)
        else:
            wrapper = CachedLRUAsyncCallable(function, typed=typed, maxsize=cast('int', maxsize))

        update_wrapper(cast('Any', wrapper), function)

        return cast('LRUAsyncCallable[AC]', wrapper)

    return lru_decorator


class BaseLRUAsyncCallable[AC: Callable[..., Any]]:
    __slots__ = ('__dict__', '__weakref__', '__wrapped__', '_hits', '_in_flight', '_misses', '_typed')

    def __init__(self, func: AC, *, typed: bool):
        self.__wrapped__ = func
        self._hits = 0
        self._misses = 0
        self._typed = typed
        self._in_flight: dict[Hashable, asyncio.Future[Any]] = {}

    def __get__(self, instance: object, owner: type | None = None) -> Any:
        if instance is None:
            return self

        return LRUAsyncBoundCallable(cast('LRUAsyncCallable[AC]', self), instance)

    def _make_key(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Hashable:
        return _make_key(args, kwargs, typed=self._typed)

    def cache_clear(self) -> None:
        self._hits = 0
        self._misses = 0
        self._in_flight.clear()

    async def _wait_in_flight(self, key: Hashable) -> Any:
        return await self._in_flight[key]


class UncachedLRUAsyncCallable(BaseLRUAsyncCallable[AC]):
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self._misses += 1

        return await self.__wrapped__(*args, **kwargs)

    def cache_parameters(self) -> CacheParameters:
        return CacheParameters(maxsize=0, typed=self._typed)

    def cache_info(self) -> CacheInfo:
        return CacheInfo(0, self._misses, 0, 0)

    def cache_discard(self, *args: Any, **kwargs: Any) -> None:
        pass


class MemoizedLRUAsyncCallable(BaseLRUAsyncCallable[AC]):
    def __init__(self, func: AC, *, typed: bool):
        super().__init__(func, typed=typed)
        self._cache: dict[Hashable, Any] = {}

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        key = self._make_key(args, kwargs)

        if key in self._cache:
            self._hits += 1
            return self._cache[key]

        if key in self._in_flight:
            return await self._in_flight[key]

        self._misses += 1
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._in_flight[key] = future

        try:
            result = await self.__wrapped__(*args, **kwargs)

            self._cache[key] = result
            future.set_result(result)
        except Exception as exc:
            future.set_exception(exc)
            raise
        else:
            return result
        finally:
            if self._in_flight.get(key) is future:
                del self._in_flight[key]

    def cache_parameters(self) -> CacheParameters:
        return CacheParameters(maxsize=None, typed=self._typed)

    def cache_info(self) -> CacheInfo:
        return CacheInfo(self._hits, self._misses, None, len(self._cache))

    def cache_clear(self) -> None:
        super().cache_clear()
        self._cache.clear()

    def cache_discard(self, *args: Any, **kwargs: Any) -> None:
        key = self._make_key(args, kwargs)
        self._cache.pop(key, None)


class CachedLRUAsyncCallable(BaseLRUAsyncCallable[AC]):
    def __init__(self, func: AC, *, typed: bool, maxsize: int):
        super().__init__(func, typed=typed)
        self._maxsize = maxsize
        self._cache: OrderedDict[Hashable, Any] = OrderedDict()

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        key = self._make_key(args, kwargs)

        if key in self._cache:
            self._hits += 1
            self._cache.move_to_end(key)

            return self._cache[key]

        if key in self._in_flight:
            return await self._in_flight[key]

        self._misses += 1
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._in_flight[key] = future

        try:
            result = await self.__wrapped__(*args, **kwargs)

            future.set_result(result)

            if key not in self._cache:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
                self._cache[key] = result
        except Exception as exc:
            future.set_exception(exc)
            raise
        else:
            return result
        finally:
            if self._in_flight.get(key) is future:
                del self._in_flight[key]

    def cache_parameters(self) -> CacheParameters:
        return CacheParameters(maxsize=self._maxsize, typed=self._typed)

    def cache_info(self) -> CacheInfo:
        return CacheInfo(self._hits, self._misses, self._maxsize, len(self._cache))

    def cache_clear(self) -> None:
        super().cache_clear()
        self._cache.clear()

    def cache_discard(self, *args: Any, **kwargs: Any) -> None:
        key = self._make_key(args, kwargs)
        self._cache.pop(key, None)
