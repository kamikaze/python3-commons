import asyncio
import warnings
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from functools import update_wrapper
from typing import TYPE_CHECKING, Any, Concatenate, NamedTuple, Self, TypedDict, TypeVar, cast, overload

__all__ = [
    'AsyncLRUCache',
    'BoundAsyncLRUCache',
    'CacheInfo',
    'CacheParameters',
    'LRUAsyncCallable',  # deprecated alias, kept in __all__ until 1.0 so star imports keep binding it (C2)
    'async_lru_cache',
]

_KWD_MARK = (object(),)
_FAST_TYPES = frozenset({int, str})


def _make_key(args: tuple[Any, ...], kwargs: dict[str, Any], *, typed: bool) -> Hashable:
    """
    Make a cache key from positional and keyword arguments, flat like functools._make_key (which is private)

    Keyword order matters: f(x=1, y=2) and f(y=2, x=1) are cached separately. A single int or str argument is its
    own key.
    """
    key: tuple[Any, ...] = args

    if kwargs:
        key += _KWD_MARK

        for item in kwargs.items():
            key += item

    if typed:
        key += tuple(type(v) for v in args)

        if kwargs:
            key += tuple(type(v) for v in kwargs.values())
    elif len(key) == 1 and type(key[0]) in _FAST_TYPES:
        return key[0]

    return key


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


@dataclass(slots=True)
class _InFlight:
    """The Task computing one cache key on one event loop, and how many callers wait for it"""

    task: asyncio.Task[Any]
    waiters: int = 0


class AsyncLRUCache[**P, R]:
    """
    A LRU cache wrapping an async function, typed with the wrapped function's parameters and result

    Instances are what ``async_lru_cache`` returns. A cache on a method is shared by all instances of the class and
    keys on ``self``, so the cache holds a strong reference to every instance it has a live entry for. Those
    instances stay alive until their entries are evicted or ``cache_clear`` is called.
    """

    __slots__ = ('__dict__', '__weakref__', '__wrapped__', '_hits', '_in_flight', '_misses', '_typed')

    __wrapped__: Callable[P, Awaitable[R]]

    def __init__(self, func: Callable[P, Awaitable[R]], *, typed: bool) -> None:
        self.__wrapped__ = func
        self._hits = 0
        self._misses = 0
        self._typed = typed
        self._in_flight: dict[tuple[asyncio.AbstractEventLoop, Hashable], _InFlight] = {}

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__[S, **Q](
        self: AsyncLRUCache[Concatenate[S, Q], R], instance: S, owner: type | None = None
    ) -> BoundAsyncLRUCache[Q, R]: ...

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self

        return BoundAsyncLRUCache(cast('AsyncLRUCache[Concatenate[Any, ...], R]', self), instance)

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        raise NotImplementedError

    def _make_key(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Hashable:
        return _make_key(args, kwargs, typed=self._typed)

    def cache_parameters(self) -> CacheParameters:
        raise NotImplementedError

    def cache_info(self) -> CacheInfo:
        raise NotImplementedError

    def cache_clear(self) -> None:
        self._hits = 0
        self._misses = 0
        self._in_flight.clear()

    def cache_discard(self, *args: P.args, **kwargs: P.kwargs) -> None:
        raise NotImplementedError


class BoundAsyncLRUCache[**Q, R]:
    """An AsyncLRUCache that is bound like a method: ``self`` is dropped from the parameters"""

    __slots__ = ('__self__', '__weakref__', '_lru')

    def __init__(self, lru: AsyncLRUCache[Concatenate[Any, Q], R], __self__: object) -> None:
        self._lru = lru
        self.__self__ = __self__

    @property
    def __wrapped__(self) -> Callable[Concatenate[Any, Q], Awaitable[R]]:
        return self._lru.__wrapped__

    @property
    def __func__(self) -> AsyncLRUCache[Concatenate[Any, Q], R]:
        return self._lru

    def __get__(self, instance: object, owner: type | None = None) -> BoundAsyncLRUCache[Q, R]:
        return BoundAsyncLRUCache(self._lru, instance)

    async def __call__(self, *args: Q.args, **kwargs: Q.kwargs) -> R:
        return await self._lru(self.__self__, *args, **kwargs)

    def cache_parameters(self) -> CacheParameters:
        return self._lru.cache_parameters()

    def cache_info(self) -> CacheInfo:
        return self._lru.cache_info()

    def cache_clear(self) -> None:
        return self._lru.cache_clear()

    def cache_discard(self, *args: Q.args, **kwargs: Q.kwargs) -> None:
        return self._lru.cache_discard(self.__self__, *args, **kwargs)

    def __repr__(self) -> str:
        name = getattr(self.__wrapped__, '__qualname__', '?')

        return f'<bound async cache {name} of {self.__self__}>'

    def __getattr__(self, name: str) -> Any:
        return getattr(self._lru, name)


@overload
def async_lru_cache[**P, R](maxsize: Callable[P, Awaitable[R]], /) -> AsyncLRUCache[P, R]: ...


@overload
def async_lru_cache[**P, R](
    maxsize: int | None = 128, *, typed: bool = False
) -> Callable[[Callable[P, Awaitable[R]]], AsyncLRUCache[P, R]]: ...


def async_lru_cache[**P, R](
    maxsize: int | Callable[P, Awaitable[R]] | None = 128, *, typed: bool = False
) -> AsyncLRUCache[P, R] | Callable[[Callable[P, Awaitable[R]]], AsyncLRUCache[P, R]]:
    """
    Least Recently Used cache for async functions

    Concurrent calls with the same key share one computation. It runs in a Task that the cache owns, so
    cancelling one caller (or a timeout in one caller) does not affect the others. The computation is
    cancelled only when its last waiting caller goes away. Failures are not cached.

    Caveats:

    * The computation runs in a copy of the first caller's context. Context variables that the cached function
      sets do not reach the caller, and later callers get a result computed in the first caller's context. Do not
      cache functions that depend on request-scoped context variables.
    * Callers on different event loops never share a running computation, but cached values are shared by every
      loop in the process.
    * A cache instance is not thread-safe. Using one from several threads at once can raise ``RuntimeError``;
      this is a known issue and is not addressed.
    * On a method the cache is shared by all instances and keys on ``self``, which keeps every instance with a
      live entry alive until the entry is evicted or ``cache_clear`` is called.
    """
    if callable(maxsize):
        # used as function decorator, first arg is the function to be wrapped
        return async_lru_cache(typed=typed)(cast('Callable[P, Awaitable[R]]', maxsize))

    if maxsize is not None and not isinstance(maxsize, int):
        msg = f"first argument to 'async_lru_cache' must be an int, a callable or None, not {type(maxsize).__name__}"
        raise TypeError(msg)

    def lru_decorator(function: Callable[P, Awaitable[R]]) -> AsyncLRUCache[P, R]:
        wrapper: AsyncLRUCache[P, R]

        if maxsize is not None and maxsize <= 0:
            wrapper = _UncachedAsyncLRUCache(function, typed=typed)
        else:
            wrapper = _CachedAsyncLRUCache(function, typed=typed, maxsize=maxsize)

        update_wrapper(cast('Any', wrapper), function)

        return wrapper

    return lru_decorator


class _UncachedAsyncLRUCache[**P, R](AsyncLRUCache[P, R]):
    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        self._misses += 1

        return await self.__wrapped__(*args, **kwargs)

    def cache_parameters(self) -> CacheParameters:
        return CacheParameters(maxsize=0, typed=self._typed)

    def cache_info(self) -> CacheInfo:
        return CacheInfo(0, self._misses, 0, 0)

    def cache_discard(self, *args: P.args, **kwargs: P.kwargs) -> None:
        pass


class _CachedAsyncLRUCache[**P, R](AsyncLRUCache[P, R]):
    def __init__(self, func: Callable[P, Awaitable[R]], *, typed: bool, maxsize: int | None) -> None:
        super().__init__(func, typed=typed)
        self._maxsize = maxsize
        self._cache: OrderedDict[Hashable, R] = OrderedDict()

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        key = self._make_key(args, kwargs)

        if key in self._cache:
            self._hits += 1
            self._cache.move_to_end(key)

            return self._cache[key]

        loop = asyncio.get_running_loop()
        flight_key = (loop, key)
        entry = self._in_flight.get(flight_key)

        if entry is None:
            self._misses += 1
            task = loop.create_task(self._run(flight_key, key, args, kwargs))
            entry = _InFlight(task)

            # With an eager task factory the task may already be finished, and _run has then run its cleanup.
            if not task.done():
                self._in_flight[flight_key] = entry

        entry.waiters += 1

        try:
            return await asyncio.shield(entry.task)
        finally:
            entry.waiters -= 1

            if entry.waiters == 0 and not entry.task.done():
                # Last waiter is gone. Unregister first so that a later caller starts a new computation
                # instead of joining the one being cancelled.
                if self._in_flight.get(flight_key) is entry:
                    del self._in_flight[flight_key]

                entry.task.cancel()

    async def _run(
        self,
        flight_key: tuple[asyncio.AbstractEventLoop, Hashable],
        key: Hashable,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        try:
            result = await self.__wrapped__(*args, **kwargs)

            if key not in self._cache:
                if self._maxsize is not None and len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)

                self._cache[key] = result

            return result
        finally:
            entry = self._in_flight.get(flight_key)

            if entry is not None and entry.task is asyncio.current_task():
                del self._in_flight[flight_key]

    def cache_parameters(self) -> CacheParameters:
        return CacheParameters(maxsize=self._maxsize, typed=self._typed)

    def cache_info(self) -> CacheInfo:
        return CacheInfo(self._hits, self._misses, self._maxsize, len(self._cache))

    def cache_clear(self) -> None:
        super().cache_clear()
        self._cache.clear()

    def cache_discard(self, *args: P.args, **kwargs: P.kwargs) -> None:
        key = self._make_key(args, kwargs)
        self._cache.pop(key, None)


# Deprecated names, removed in 1.0. Declared for type checkers, served at runtime with a DeprecationWarning.
if TYPE_CHECKING:
    AC_invariant = TypeVar('AC_invariant', bound=Callable[..., Any])
    AC = TypeVar('AC', bound=Callable[..., Any])
    LRUAsyncCallable = AsyncLRUCache
    LRUAsyncBoundCallable = BoundAsyncLRUCache
    BaseLRUAsyncCallable = AsyncLRUCache
    UncachedLRUAsyncCallable = _UncachedAsyncLRUCache
    CachedLRUAsyncCallable = _CachedAsyncLRUCache

_DEPRECATED_CLASSES: dict[str, tuple[type, str]] = {
    'LRUAsyncCallable': (AsyncLRUCache, 'AsyncLRUCache'),
    'LRUAsyncBoundCallable': (BoundAsyncLRUCache, 'BoundAsyncLRUCache'),
    'BaseLRUAsyncCallable': (AsyncLRUCache, 'AsyncLRUCache'),
    'UncachedLRUAsyncCallable': (_UncachedAsyncLRUCache, 'AsyncLRUCache'),
    'CachedLRUAsyncCallable': (_CachedAsyncLRUCache, 'AsyncLRUCache'),
}
_AC = TypeVar('_AC', bound=Callable[..., Any])
_AC_INVARIANT = TypeVar('_AC_INVARIANT', bound=Callable[..., Any])
_DEPRECATED_TYPEVARS: dict[str, TypeVar] = {'AC': _AC, 'AC_invariant': _AC_INVARIANT}


def __getattr__(name: str) -> object:
    """Serve the pre-0.29 class names and TypeVars with a DeprecationWarning"""
    if name in _DEPRECATED_CLASSES:
        value, replacement = _DEPRECATED_CLASSES[name]
        message = f'python3_commons.async_functools.{name} is deprecated and will be removed in 1.0; use {replacement}'
    elif name in _DEPRECATED_TYPEVARS:
        value = _DEPRECATED_TYPEVARS[name]
        message = (
            f'python3_commons.async_functools.{name} is deprecated and will be removed in 1.0; '
            'AsyncLRUCache is generic over ParamSpec and the result type'
        )
    else:
        msg = f'module {__name__!r} has no attribute {name!r}'

        raise AttributeError(msg)

    warnings.warn(message, DeprecationWarning, stacklevel=2)

    return value
