import asyncio
import functools
import logging
import socket
import sys
import time
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from types import TracebackType
from typing import Any, Concatenate, Self, cast, overload

import msgspec
from pydantic import RedisDsn
from pydantic import ValidationError as PydanticValidationError

from python3_commons._extras import require

require('cache', 'valkey')

# PEP 810 (3.15): `import python3_commons.cache` does not import valkey until a valkey name is first used; 3.14 ignores
# this list and imports eagerly. Nothing here may use a valkey name at import time (no base classes, no module-level
# tuples of exceptions). Exact submodule paths, never the `lazy` keyword (3.14 must parse this file).
__lazy_modules__ = [
    'valkey.asyncio',
    'valkey.asyncio.client',
    'valkey.asyncio.retry',
    'valkey.asyncio.sentinel',
    'valkey.backoff',
    'valkey.exceptions',
]

from valkey.asyncio import BlockingConnectionPool, Sentinel, Valkey
from valkey.asyncio.client import Pipeline
from valkey.asyncio.retry import Retry
from valkey.asyncio.sentinel import SentinelConnectionPool
from valkey.backoff import FullJitterBackoff
from valkey.exceptions import AuthenticationError as ValkeyAuthenticationError
from valkey.exceptions import BusyLoadingError as ValkeyBusyLoadingError
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from python3_commons.conf import ValkeySettings, get_valkey_settings
from python3_commons.serializers.msgspec import deserialize_msgpack_native, serialize_msgpack_native

logger = logging.getLogger(__name__)

__all__ = [
    'AsyncValkeyClient',
    'ValkeyCache',
    'add_set_item',
    'append_sequence',
    'close_client',
    'close_default_cache',
    'delete',
    'delete_dict_item',
    'delete_set_item',
    'exists',
    'get',
    'get_bytes',
    'get_default_cache',
    'get_dict',
    'get_dict_item',
    'get_sequence',
    'get_set_members',
    'get_string',
    'get_valkey_client',
    'has_set_item',
    'scan',
    'set_default_cache',
    'set_dict_item',
    'store',
    'store_bytes',
    'store_dict',
    'store_sequence',
    'store_set',
    'store_string',
    'update_dict',
]


_clients: dict[asyncio.AbstractEventLoop, Valkey] = {}
_sentinels: dict[asyncio.AbstractEventLoop, Sentinel] = {}


def _get_keepalive_options() -> dict[int, int]:
    if sys.platform == 'linux':
        return {socket.TCP_KEEPIDLE: 10, socket.TCP_KEEPINTVL: 5, socket.TCP_KEEPCNT: 5}

    return {}


def _build_sentinel_client(dsn: RedisDsn) -> tuple[Valkey, Sentinel]:
    sentinel_client = Sentinel(
        [(dsn.host, dsn.port)],
        socket_connect_timeout=10,
        socket_timeout=60,
        password=dsn.password,
        sentinel_kwargs={'password': dsn.password},
    )
    client = sentinel_client.master_for(
        'myprimary',
        valkey_class=Valkey,
        socket_connect_timeout=10,
        socket_timeout=60,
        health_check_interval=30,
        retry_on_timeout=True,
        retry=Retry(FullJitterBackoff(cap=5, base=1), 5),
        socket_keepalive=True,
        socket_keepalive_options=_get_keepalive_options(),
    )

    return client, sentinel_client


def _evict_closed_loops() -> None:
    for loop, _ in list(_clients.items()):
        if loop.is_closed():
            del _clients[loop]
            _sentinels.pop(loop, None)


def get_valkey_client() -> Valkey:
    """Return the Valkey client of the running event loop, creating it on first use.

    Clients are not shared between loops. Entries of closed loops are evicted on every lookup; call
    :func:`close_client` before the loop ends to close the connections gracefully.
    """
    loop = asyncio.get_running_loop()
    _evict_closed_loops()

    if (client := _clients.get(loop)) is not None:
        return client

    valkey_settings = get_valkey_settings()

    if valkey_settings.sentinel_dsn:
        client, sentinel_client = _build_sentinel_client(valkey_settings.sentinel_dsn)
        _sentinels[loop] = sentinel_client
    elif valkey_settings.dsn:
        client = Valkey.from_url(str(valkey_settings.dsn))
    else:
        msg = 'Set VALKEY_DSN or VALKEY_SENTINEL_DSN'
        raise ValueError(msg)

    _clients[loop] = client

    return client


async def close_client() -> None:
    """Close the current loop's client (and sentinel connections) and forget it."""
    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    sentinel_client = _sentinels.pop(loop, None)

    if client is not None:
        await client.aclose()

    if sentinel_client is not None:
        for node in sentinel_client.sentinels:
            await node.aclose()


@warnings.deprecated('AsyncValkeyClient is deprecated and will be removed in 1.0; use get_valkey_client()')
class AsyncValkeyClient:
    def __init__(self, dsn: RedisDsn | None = None, sentinel_dsn: RedisDsn | None = None) -> None:
        self._dsn = dsn
        self._sentinel_dsn = sentinel_dsn

    def get_client(self) -> Valkey:
        return get_valkey_client()


_replace_warned = False
_DECODE_ERRORS = (msgspec.DecodeError, PydanticValidationError)


def _warn(message: str) -> None:
    # Skip every frame in this module, so the warning points at the caller of the public function.
    warnings.warn(message, DeprecationWarning, skip_file_prefixes=(__file__,))


def _ttl_seconds(ttl: int | timedelta) -> float:
    return ttl.total_seconds() if isinstance(ttl, timedelta) else ttl


def _check_ttl(ttl: int | timedelta | None, *, collection: bool) -> None:
    """Warn about a non-positive TTL; 1.0 raises ValueError instead (5.3)."""
    if ttl is None or _ttl_seconds(ttl) > 0:
        return

    if collection:
        if not ttl:
            _warn('ttl=0 on a collection write (treated as no expiry) raises ValueError in 1.0')
    else:
        _warn('ttl <= 0 raises ValueError in 1.0')


def _warn_replace_once() -> None:
    global _replace_warned  # noqa: PLW0603

    if not _replace_warned:
        _replace_warned = True
        _warn(
            'Writing a collection to an existing key replaces instead of merging, and returns bool, in 1.0; '
            'use append_sequence/update_dict to keep merging'
        )


@overload
def _graceful[**P, R]() -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R | None]]]: ...
@overload
def _graceful[**P, R, D](default: D) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R | D]]]: ...
def _graceful(default: Any = None) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Log Valkey connection failures and return *default* instead of raising."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except (ValkeyConnectionError, ValkeyTimeoutError) as exc:
                logger.exception('Cache operation %s failed.', getattr(func, '__qualname__', repr(func)))

                if isinstance(exc, ValkeyAuthenticationError):
                    _warn('Swallowing the Valkey AuthenticationError (a configuration error) raises it in 1.0')

                return default

        return wrapper

    return decorator


def _decode(name: str, data: bytes, data_type: Any = None) -> tuple[bool, Any]:
    """Decode *data*; return ``(True, value)``, or ``(False, None)`` (with a WARNING) when it does not decode."""
    try:
        return True, deserialize_msgpack_native(data, data_type)
    except _DECODE_ERRORS as exc:
        logger.warning('Cache value of %s could not be decoded, treating it as a miss: %s', name, exc)

        return False, None


async def _write_collection(
    name: str,
    ttl: int | timedelta | None,
    queue: Callable[[Pipeline], object],
    *,
    warn_on_existing: bool,
) -> None:
    """Queue *queue* and EXPIRE in one MULTI/EXEC, so a failed EXPIRE never leaves a key without its TTL."""
    _check_ttl(ttl, collection=True)

    async with get_valkey_client().pipeline(transaction=True) as pipe:
        pipe.exists(name)
        queue(pipe)

        if ttl:
            pipe.expire(name, ttl)

        existed = (await pipe.execute())[0]

    if warn_on_existing and existed:
        _warn_replace_once()


@_graceful(default=cast('tuple[int, tuple[()]]', (0, ())))
async def scan(
    cursor: int = 0,
    match: bytes | str | memoryview | None = None,
    count: int | None = None,
    _type: str | None = None,
    **kwargs: Any,
) -> tuple[int, list[bytes]]:
    return cast(
        'tuple[int, list[bytes]]', await _result(get_valkey_client().scan(cursor, match, count, _type, **kwargs))
    )


@_graceful()
async def delete(*names: str | bytes | memoryview) -> None:
    await _result(get_valkey_client().delete(*names))


@_graceful(default=False)
async def exists(name: str) -> bool:
    return await _result(get_valkey_client().exists(name)) == 1


@_graceful()
async def store_bytes(
    name: str, data: bytes, ttl: int | timedelta | None = None, *, if_not_set: bool = False
) -> bool | None:
    _check_ttl(ttl, collection=False)

    return await _result(get_valkey_client().set(name, data, ex=ttl, nx=if_not_set))


@_graceful()
async def get_bytes(name: str) -> bytes | None:
    return await _result(get_valkey_client().get(name))


async def store(name: str, obj: Any, ttl: int | timedelta | None = None, *, if_not_set: bool = False) -> bool | None:
    return await store_bytes(name, serialize_msgpack_native(obj), ttl, if_not_set=if_not_set)


@overload
async def get(name: str, default: Any = None, data_type: None = None) -> Any: ...
@overload
async def get[T](name: str, default: None = None, *, data_type: type[T]) -> T | None: ...
@overload
async def get[T, D](name: str, default: D, data_type: type[T]) -> T | D: ...
@overload
async def get(name: str, default: Any = None, data_type: Any = None) -> Any: ...
async def get(name: str, default: Any = None, data_type: Any = None) -> Any:
    if data := await get_bytes(name):
        ok, value = _decode(name, data, data_type)

        if ok:
            return value

    return default


async def store_string(name: str, data: str, ttl: int | timedelta | None = None) -> None:
    await store_bytes(name, data.encode(), ttl)


async def get_string(name: str) -> str | None:
    if (data := await get_bytes(name)) is not None:
        return data.decode('utf-8')

    return None


@_graceful()
async def store_sequence(name: str, data: Sequence[Any], ttl: int | timedelta | None = None) -> None:
    if data:
        await _write_collection(
            name, ttl, lambda p: p.rpush(name, *map(serialize_msgpack_native, data)), warn_on_existing=True
        )


@_graceful()
async def append_sequence(name: str, *items: Any, ttl: int | timedelta | None = None) -> None:
    """Append *items* to the list at *name* (merge), refreshing the TTL when given; never warns."""
    if items:
        await _write_collection(
            name, ttl, lambda p: p.rpush(name, *map(serialize_msgpack_native, items)), warn_on_existing=False
        )


@_graceful(default=())
async def _lrange(name: str) -> Sequence[bytes]:
    return await _result(get_valkey_client().lrange(name, 0, -1))


async def get_sequence(
    name: str, container: type = list, *, data_type: Any = None, _type: type | None = None
) -> Sequence[Any]:
    """Return the list at *name* as a *container*. The ``_type`` keyword is the deprecated name of *container*."""
    if _type is not None:
        _warn('The get_sequence() keyword _type is renamed container and _type is removed in 1.0')
        container = _type

    values = []

    for raw in await _lrange(name):
        ok, value = _decode(name, raw, data_type)

        if not ok:
            return container()

        values.append(value)

    return container(values)


def _hset_mapping(mapping: Mapping[Any, Any]) -> dict[str, bytes]:
    return {str(k): serialize_msgpack_native(v) for k, v in mapping.items()}


@_graceful()
async def store_dict(name: str, data: Mapping[Any, Any], ttl: int | timedelta | None = None) -> None:
    if data:
        await _write_collection(name, ttl, lambda p: p.hset(name, mapping=_hset_mapping(data)), warn_on_existing=True)


@_graceful()
async def update_dict(name: str, mapping: Mapping[Any, Any], ttl: int | timedelta | None = None) -> None:
    """Set the fields of *mapping* in the hash at *name* (merge), refreshing the TTL when given; never warns."""
    if mapping:
        await _write_collection(
            name, ttl, lambda p: p.hset(name, mapping=_hset_mapping(mapping)), warn_on_existing=False
        )


@_graceful()
async def _hgetall(name: str) -> dict[bytes, bytes]:
    return await _result(get_valkey_client().hgetall(name))


async def get_dict(name: str, value_data_type: Any = None) -> dict[Any, Any] | None:
    if data := await _hgetall(name):
        result = {}

        for k, raw in data.items():
            ok, value = _decode(name, raw, value_data_type)

            if not ok:
                return None

            result[k.decode()] = value

        return result

    return None


@_graceful()
async def _hget(name: str, key: str) -> bytes | None:
    return cast('bytes | None', await _result(get_valkey_client().hget(name, key)))


@overload
async def get_dict_item(name: str, key: str, data_type: None = None, default: Any = None) -> Any: ...
@overload
async def get_dict_item[T](name: str, key: str, data_type: type[T], default: None = None) -> T | None: ...
@overload
async def get_dict_item[T, D](name: str, key: str, data_type: type[T], default: D) -> T | D: ...
@overload
async def get_dict_item(name: str, key: str, data_type: Any = None, default: Any = None) -> Any: ...
async def get_dict_item(name: str, key: str, data_type: Any = None, default: Any = None) -> Any:
    if data := await _hget(name, key):
        ok, value = _decode(name, data, data_type)

        if ok:
            return value

    return default


@_graceful()
async def set_dict_item(name: str, key: str, obj: Any) -> None:
    # the value is msgpack bytes; valkey's stub types it as str
    await _result(get_valkey_client().hset(name, key, serialize_msgpack_native(obj)))  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


@_graceful()
async def delete_dict_item(name: str, *keys: str) -> None:
    await _result(get_valkey_client().hdel(name, *keys))


@_graceful()
async def store_set(name: str, value: set[Any], ttl: int | timedelta | None = None) -> None:
    if value:
        await _write_collection(
            name, ttl, lambda p: p.sadd(name, *map(serialize_msgpack_native, value)), warn_on_existing=True
        )


@_graceful(default=False)
async def has_set_item(name: str, value: str) -> bool:
    # members are stored as msgpack bytes; valkey's stub types the value as str
    return await _result(get_valkey_client().sismember(name, serialize_msgpack_native(value))) == 1  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


@_graceful()
async def add_set_item(name: str, *values: str) -> None:
    await _result(get_valkey_client().sadd(name, *map(serialize_msgpack_native, values)))


@_graceful()
async def delete_set_item(name: str, value: str) -> None:
    await _result(get_valkey_client().srem(name, serialize_msgpack_native(value)))


@_graceful()
async def _smembers(name: str) -> set[bytes] | None:
    return await _result(get_valkey_client().smembers(name))


async def get_set_members(name: str, data_type: Any = None) -> set[Any] | None:
    if (raw_members := await _smembers(name)) is None:
        return None

    members = set()

    for raw in raw_members:
        ok, value = _decode(name, raw, data_type)

        if not ok:
            return None

        members.add(value)

    return members


# ValkeyCache: the 1.0 contract. The module functions above keep the 0.29.x one until 5.3 delegates them to it.

_FAILURE_LOG_INTERVAL = 60.0  # seconds between the repeated "operation failed" warnings of one cache


@functools.cache
def _sentinel_blocking_pool_class() -> type[SentinelConnectionPool]:
    """A bounded pool that waits for a free connection, for the sentinel-managed master.

    Defined on first use: a base class would import valkey when this module is imported.
    """

    class SentinelBlockingConnectionPool(SentinelConnectionPool, BlockingConnectionPool):
        pass

    return SentinelBlockingConnectionPool


def _connection_kwargs(settings: ValkeySettings) -> dict[str, Any]:
    """The connection options, the same for a plain DSN and for sentinel mode."""
    kwargs: dict[str, Any] = {
        'socket_timeout': settings.socket_timeout,
        'socket_connect_timeout': settings.socket_connect_timeout,
        'health_check_interval': settings.health_check_interval,
        'socket_keepalive': True,
        'socket_keepalive_options': _get_keepalive_options(),
    }

    if settings.retries:
        kwargs['retry_on_timeout'] = True
        kwargs['retry'] = Retry(FullJitterBackoff(cap=5, base=1), settings.retries)

    return kwargs


def _build_client(settings: ValkeySettings) -> tuple[Valkey, Sentinel | None]:
    """Build the client (bounded or not) for *settings*; raise RuntimeError when no server is configured."""
    kwargs = _connection_kwargs(settings)
    pool_kwargs: dict[str, Any] = (
        {}
        if settings.max_connections is None
        else {'max_connections': settings.max_connections, 'timeout': settings.pool_timeout}
    )

    if sentinel_dsn := settings.sentinel_dsn:
        sentinel_password = settings.sentinel_password.get_secret_value() if settings.sentinel_password else None
        sentinel_client = Sentinel(
            settings.sentinel_nodes,
            password=sentinel_dsn.password,
            sentinel_kwargs={
                'password': sentinel_password or sentinel_dsn.password,
                'socket_timeout': settings.socket_timeout,
                'socket_connect_timeout': settings.socket_connect_timeout,
            },
            **kwargs,
        )
        client = sentinel_client.master_for(
            settings.sentinel_service_name,
            valkey_class=Valkey,
            connection_pool_class=SentinelConnectionPool if not pool_kwargs else _sentinel_blocking_pool_class(),
            **pool_kwargs,
        )

        return client, sentinel_client

    if settings.sentinels:
        msg = 'Set VALKEY_SENTINEL_DSN together with VALKEY_SENTINELS'
        raise RuntimeError(msg)

    if dsn := settings.dsn:
        if pool_kwargs:
            return Valkey.from_pool(BlockingConnectionPool.from_url(str(dsn), **pool_kwargs, **kwargs)), None

        return Valkey.from_url(str(dsn), **kwargs), None

    msg = 'Set VALKEY_DSN or VALKEY_SENTINEL_DSN'
    raise RuntimeError(msg)


async def _result[T](result: Awaitable[T] | T) -> T:
    """Await a client command: valkey types them as ``Awaitable[T] | T``, the asyncio client returns awaitables."""
    return await cast('Awaitable[T]', result)


def _require_positive_ttl(ttl: int | timedelta | None) -> None:
    if ttl is not None and _ttl_seconds(ttl) <= 0:
        msg = f'ttl must be positive, got {ttl!r}'
        raise ValueError(msg)


def _fail_open[S: ValkeyCache, **P, R](
    default: Any,
) -> Callable[[Callable[Concatenate[S, P], Awaitable[R]]], Callable[Concatenate[S, P], Awaitable[R]]]:
    """Make a ValkeyCache method return *default* on a transient error when the cache is ``fail_open``."""

    def decorator(func: Callable[Concatenate[S, P], Awaitable[R]]) -> Callable[Concatenate[S, P], Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await func(self, *args, **kwargs)
            except (ValkeyConnectionError, ValkeyTimeoutError, ValkeyBusyLoadingError) as exc:
                # A wrong password is a configuration error, so it is never swallowed
                if not self.fail_open or isinstance(exc, ValkeyAuthenticationError):
                    raise

                self._log_failure(getattr(func, '__name__', 'operation'), exc)  # pyright: ignore[reportPrivateUsage]

                return default

        return wrapper

    return decorator


class ValkeyCache:
    """Typed cache on a Valkey client, under the 1.0 contract.

    * ``store_*`` return ``bool`` and ``delete`` returns ``int``;
    * collection writes replace what is stored under the key (one transaction: DEL, write, EXPIRE), an empty
      collection deletes the key, and :meth:`append_sequence` / :meth:`update_dict` merge instead;
    * ``ttl`` must be positive: ``ValueError`` otherwise, before the server is contacted;
    * with ``fail_open=True`` (the default) transient errors (``ConnectionError`` except ``AuthenticationError``,
      ``TimeoutError``, ``BusyLoadingError``) are swallowed and the method returns its miss value: ``False``,
      ``0``, ``None`` or the given default. The first failure of a cache is logged as a WARNING with its
      traceback, later ones at most once per minute. With ``fail_open=False`` they propagate.

    The cache is bound to the event loop that first uses its connections: create one per loop (see
    :func:`get_default_cache`). :meth:`from_settings` builds and owns the client, so :meth:`aclose` (or
    ``async with``) closes it; a client passed to the constructor stays the caller's.
    """

    def __init__(self, client: Valkey, *, fail_open: bool = True, owns_client: bool = False) -> None:
        self._client = client
        self._owns_client = owns_client
        self._sentinel: Sentinel | None = None
        self.fail_open = fail_open
        self._last_failure_log: float | None = None
        self._suppressed_failures = 0

    @classmethod
    def from_settings(cls, settings: ValkeySettings | None = None, *, fail_open: bool = True) -> Self:
        """Build a cache that owns a new client for *settings* (``get_valkey_settings()`` by default).

        Raises ``RuntimeError`` when neither ``VALKEY_DSN`` nor ``VALKEY_SENTINEL_DSN`` is set.
        """
        client, sentinel_client = _build_client(settings if settings is not None else get_valkey_settings())
        cache = cls(client, fail_open=fail_open, owns_client=True)
        cache._sentinel = sentinel_client

        return cache

    @property
    def client(self) -> Valkey:
        """The underlying client, for commands this class does not wrap."""
        return self._client

    async def aclose(self) -> None:
        """Close the client and the sentinel connections when this cache built them; safe to call twice."""
        sentinel_client, self._sentinel = self._sentinel, None

        if self._owns_client:
            await self._client.aclose()

        if sentinel_client is not None:
            for node in sentinel_client.sentinels:
                await node.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    def _log_failure(self, operation: str, exc: BaseException) -> None:
        now = time.monotonic()

        if self._last_failure_log is None:
            logger.warning('Cache operation %s failed: %s', operation, exc, exc_info=exc)
        elif now - self._last_failure_log >= _FAILURE_LOG_INTERVAL:
            logger.warning(
                'Cache operation %s failed (%d failures since the last warning): %s',
                operation,
                self._suppressed_failures + 1,
                exc,
            )
        else:
            self._suppressed_failures += 1

            return

        self._last_failure_log = now
        self._suppressed_failures = 0

    async def _write_collection(
        self, name: str, ttl: int | timedelta | None, queue: Callable[[Pipeline], object] | None, *, replace: bool
    ) -> bool:
        """Run ``[DEL], queue, [EXPIRE]`` in one MULTI/EXEC; *queue* None only deletes the key."""
        _require_positive_ttl(ttl)

        async with self._client.pipeline(transaction=True) as pipe:
            if replace:
                pipe.delete(name)

            if queue is not None:
                queue(pipe)

                if ttl is not None:
                    pipe.expire(name, ttl)

            await pipe.execute()

        return True

    @_fail_open(default=(0, ()))
    async def scan(
        self,
        cursor: int = 0,
        match: bytes | str | memoryview | None = None,
        count: int | None = None,
        key_type: str | None = None,
        **kwargs: Any,
    ) -> tuple[int, Sequence[bytes]]:
        """One SCAN step; *key_type* is SCAN's ``TYPE`` filter."""
        return cast(
            'tuple[int, Sequence[bytes]]', await _result(self._client.scan(cursor, match, count, key_type, **kwargs))
        )

    @_fail_open(default=0)
    async def delete(self, *names: str | bytes | memoryview) -> int:
        """Delete the keys; return how many existed."""
        return await _result(self._client.delete(*names))

    @_fail_open(default=False)
    async def exists(self, name: str) -> bool:
        return await _result(self._client.exists(name)) == 1

    @_fail_open(default=False)
    async def store_bytes(
        self, name: str, data: bytes, ttl: int | timedelta | None = None, *, if_not_set: bool = False
    ) -> bool:
        """SET *name*; return False when ``if_not_set`` found the key already there."""
        _require_positive_ttl(ttl)

        return bool(await _result(self._client.set(name, data, ex=ttl, nx=if_not_set)))

    @_fail_open(default=None)
    async def get_bytes(self, name: str) -> bytes | None:
        return await _result(self._client.get(name))

    async def store(self, name: str, obj: Any, ttl: int | timedelta | None = None, *, if_not_set: bool = False) -> bool:
        return await self.store_bytes(name, serialize_msgpack_native(obj), ttl, if_not_set=if_not_set)

    @overload
    async def get(self, name: str, default: Any = None, data_type: None = None) -> Any: ...
    @overload
    async def get[T](self, name: str, default: None = None, *, data_type: type[T]) -> T | None: ...
    @overload
    async def get[T, D](self, name: str, default: D, data_type: type[T]) -> T | D: ...
    @overload
    async def get(self, name: str, default: Any = None, data_type: Any = None) -> Any: ...
    async def get(self, name: str, default: Any = None, data_type: Any = None) -> Any:
        """Return the stored value, or *default* on a miss or when the value does not decode as *data_type*."""
        if data := await self.get_bytes(name):
            ok, value = _decode(name, data, data_type)

            if ok:
                return value

        return default

    async def store_string(self, name: str, data: str, ttl: int | timedelta | None = None) -> bool:
        return await self.store_bytes(name, data.encode(), ttl)

    async def get_string(self, name: str) -> str | None:
        if (data := await self.get_bytes(name)) is not None:
            return data.decode('utf-8')

        return None

    @_fail_open(default=False)
    async def store_sequence(self, name: str, data: Sequence[Any], ttl: int | timedelta | None = None) -> bool:
        """Replace the list at *name* with *data*; an empty *data* deletes the key."""
        return await self._write_collection(
            name,
            ttl,
            (lambda p: p.rpush(name, *map(serialize_msgpack_native, data))) if data else None,
            replace=True,
        )

    @_fail_open(default=False)
    async def append_sequence(self, name: str, *items: Any, ttl: int | timedelta | None = None) -> bool:
        """Append *items* to the list at *name*, refreshing the TTL when given."""
        _require_positive_ttl(ttl)

        if not items:
            return True

        return await self._write_collection(
            name, ttl, lambda p: p.rpush(name, *map(serialize_msgpack_native, items)), replace=False
        )

    @_fail_open(default=())
    async def _lrange(self, name: str) -> Sequence[bytes]:
        return await _result(self._client.lrange(name, 0, -1))

    async def get_sequence(self, name: str, container: type = list, *, data_type: Any = None) -> Sequence[Any]:
        """Return the list at *name* as a *container* (``list`` by default); empty when missing or undecodable."""
        values = []

        for raw in await self._lrange(name):
            ok, value = _decode(name, raw, data_type)

            if not ok:
                return container()

            values.append(value)

        return container(values)

    @_fail_open(default=False)
    async def store_dict(self, name: str, data: Mapping[Any, Any], ttl: int | timedelta | None = None) -> bool:
        """Replace the hash at *name* with *data*; an empty *data* deletes the key."""
        return await self._write_collection(
            name, ttl, (lambda p: p.hset(name, mapping=_hset_mapping(data))) if data else None, replace=True
        )

    @_fail_open(default=False)
    async def update_dict(self, name: str, mapping: Mapping[Any, Any], ttl: int | timedelta | None = None) -> bool:
        """Set the fields of *mapping* in the hash at *name*, refreshing the TTL when given."""
        _require_positive_ttl(ttl)

        if not mapping:
            return True

        return await self._write_collection(
            name, ttl, lambda p: p.hset(name, mapping=_hset_mapping(mapping)), replace=False
        )

    @_fail_open(default=None)
    async def _hgetall(self, name: str) -> dict[bytes, bytes] | None:
        return await _result(self._client.hgetall(name))

    async def get_dict(self, name: str, value_data_type: Any = None) -> dict[Any, Any] | None:
        if data := await self._hgetall(name):
            result = {}

            for k, raw in data.items():
                ok, value = _decode(name, raw, value_data_type)

                if not ok:
                    return None

                result[k.decode()] = value

            return result

        return None

    @_fail_open(default=None)
    async def _hget(self, name: str, key: str) -> bytes | None:
        return cast('bytes | None', await _result(self._client.hget(name, key)))

    @overload
    async def get_dict_item(self, name: str, key: str, data_type: None = None, default: Any = None) -> Any: ...
    @overload
    async def get_dict_item[T](self, name: str, key: str, data_type: type[T], default: None = None) -> T | None: ...
    @overload
    async def get_dict_item[T, D](self, name: str, key: str, data_type: type[T], default: D) -> T | D: ...
    @overload
    async def get_dict_item(self, name: str, key: str, data_type: Any = None, default: Any = None) -> Any: ...
    async def get_dict_item(self, name: str, key: str, data_type: Any = None, default: Any = None) -> Any:
        if data := await self._hget(name, key):
            ok, value = _decode(name, data, data_type)

            if ok:
                return value

        return default

    @_fail_open(default=False)
    async def set_dict_item(self, name: str, key: str, obj: Any) -> bool:
        await _result(self._client.hset(name, mapping={key: serialize_msgpack_native(obj)}))

        return True

    @_fail_open(default=0)
    async def delete_dict_item(self, name: str, *keys: str) -> int:
        """Delete the fields; return how many existed."""
        return await _result(self._client.hdel(name, *keys))

    @_fail_open(default=False)
    async def store_set(self, name: str, value: set[Any], ttl: int | timedelta | None = None) -> bool:
        """Replace the set at *name* with *value*; an empty *value* deletes the key."""
        return await self._write_collection(
            name,
            ttl,
            (lambda p: p.sadd(name, *map(serialize_msgpack_native, value))) if value else None,
            replace=True,
        )

    @_fail_open(default=False)
    async def has_set_item(self, name: str, value: str) -> bool:
        # members are stored as msgpack bytes; valkey's stub types the value as str
        return await _result(self._client.sismember(name, serialize_msgpack_native(value))) == 1  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    @_fail_open(default=False)
    async def add_set_item(self, name: str, *values: str) -> bool:
        await _result(self._client.sadd(name, *map(serialize_msgpack_native, values)))

        return True

    @_fail_open(default=0)
    async def delete_set_item(self, name: str, value: str) -> int:
        """Remove the member; return 1 when it was in the set."""
        return await _result(self._client.srem(name, serialize_msgpack_native(value)))

    @_fail_open(default=None)
    async def _smembers(self, name: str) -> set[bytes] | None:
        return await _result(self._client.smembers(name))

    async def get_set_members(self, name: str, data_type: Any = None) -> set[Any] | None:
        if (raw_members := await self._smembers(name)) is None:
            return None

        members = set()

        for raw in raw_members:
            ok, value = _decode(name, raw, data_type)

            if not ok:
                return None

            members.add(value)

        return members


# The per-loop default ValkeyCache. Like the clients above, it is an explicit dict keyed by loop, never a weak one:
# the cache's pooled connections hold the loop, so a weak key would never be collected.
_default_caches: dict[asyncio.AbstractEventLoop, ValkeyCache] = {}


def _evict_closed_default_caches() -> None:
    for loop, _ in list(_default_caches.items()):
        if loop.is_closed():
            del _default_caches[loop]


def get_default_cache() -> ValkeyCache:
    """Return the running loop's default :class:`ValkeyCache`, building it from ``get_valkey_settings()`` on first use.

    Raises ``RuntimeError`` when no server is configured. Entries of closed loops are evicted on every lookup;
    call :func:`close_default_cache` before the loop ends to close the connections gracefully.
    """
    loop = asyncio.get_running_loop()
    _evict_closed_default_caches()

    if (default := _default_caches.get(loop)) is None:
        default = _default_caches[loop] = ValkeyCache.from_settings()

    return default


def set_default_cache(default: ValkeyCache) -> ValkeyCache | None:
    """Make *default* the running loop's default cache, e.g. one with ``fail_open=False``.

    Return the cache it replaces, if any; it is not closed, so the caller can ``await previous.aclose()``.
    """
    loop = asyncio.get_running_loop()
    _evict_closed_default_caches()
    previous = _default_caches.get(loop)
    _default_caches[loop] = default

    return previous


async def close_default_cache() -> None:
    """Close the running loop's default cache and forget it, together with the entries of closed loops."""
    loop = asyncio.get_running_loop()
    _evict_closed_default_caches()

    if (default := _default_caches.pop(loop, None)) is not None:
        await default.aclose()
