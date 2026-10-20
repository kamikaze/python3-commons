import functools
import logging
import socket
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

try:
    from valkey.asyncio import ConnectionPool, Sentinel, StrictValkey, Valkey
    from valkey.asyncio.retry import Retry
    from valkey.backoff import FullJitterBackoff
    from valkey.exceptions import ConnectionError as ValkeyConnectionError
    from valkey.exceptions import TimeoutError as ValkeyTimeoutError
except ImportError as e:
    msg = 'Install python3-commons[cache] to use this feature'
    raise RuntimeError(msg) from e

from python3_commons.conf import valkey_settings
from python3_commons.helpers import SingletonMeta
from python3_commons.serializers.msgspec import deserialize_msgpack_native, serialize_msgpack_native

if TYPE_CHECKING:
    from pydantic import RedisDsn
    from valkey.typing import ResponseT

logger = logging.getLogger(__name__)


class AsyncValkeyClient(metaclass=SingletonMeta):
    def __init__(self, dsn: RedisDsn, sentinel_dsn: RedisDsn | None) -> None:
        self._valkey_pool = None
        self._valkey = None

        if sentinel_dsn:
            self._initialize_sentinel(sentinel_dsn)
        else:
            self._initialize_standard_pool(dsn)

    @staticmethod
    def _get_keepalive_options():
        if sys.platform == 'linux':
            return {socket.TCP_KEEPIDLE: 10, socket.TCP_KEEPINTVL: 5, socket.TCP_KEEPCNT: 5}
        return {}

    def _initialize_sentinel(self, dsn: RedisDsn) -> None:
        sentinel_client = Sentinel(
            [(dsn.host, dsn.port)],
            socket_connect_timeout=10,
            socket_timeout=60,
            password=dsn.password,
            sentinel_kwargs={'password': dsn.password},
        )

        ka_options = self._get_keepalive_options()

        self._valkey = sentinel_client.master_for(
            'myprimary',
            valkey_class=StrictValkey,
            socket_connect_timeout=10,
            socket_timeout=60,
            health_check_interval=30,
            retry_on_timeout=True,
            retry=Retry(FullJitterBackoff(cap=5, base=1), 5),
            socket_keepalive=True,
            socket_keepalive_options=ka_options,
        )

    def _initialize_standard_pool(self, dsn: RedisDsn) -> None:
        self._valkey_pool = ConnectionPool.from_url(str(dsn))
        self._valkey = StrictValkey(connection_pool=self._valkey_pool)

    def get_client(self) -> Valkey:
        return self._valkey


def get_valkey_client() -> Valkey:
    return AsyncValkeyClient(valkey_settings.dsn, valkey_settings.sentinel_dsn).get_client()


def _graceful[**P, R](default: Any = None) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Log Valkey connection failures and return *default* instead of raising."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return await func(*args, **kwargs)
            except ValkeyConnectionError, ValkeyTimeoutError:
                logger.exception('Cache operation %s failed.', func.__name__)

                return default

        return wrapper

    return decorator


@_graceful(default=(0, ()))
async def scan(
    cursor: int = 0,
    match: bytes | str | memoryview | None = None,
    count: int | None = None,
    _type: str | None = None,
    **kwargs,
) -> ResponseT:
    return await get_valkey_client().scan(cursor, match, count, _type, **kwargs)


@_graceful()
async def delete(*names: str | bytes | memoryview) -> None:
    await get_valkey_client().delete(*names)


@_graceful(default=False)
async def exists(name: str) -> bool:
    return await get_valkey_client().exists(name) == 1


@_graceful()
async def store_bytes(name: str, data: bytes, ttl: int | None = None, *, if_not_set: bool = False):
    return await get_valkey_client().set(name, data, ex=ttl, nx=if_not_set)


@_graceful()
async def get_bytes(name: str) -> bytes | None:
    return await get_valkey_client().get(name)


async def store(name: str, obj: Any, ttl: int | None = None, *, if_not_set: bool = False):
    return await store_bytes(name, serialize_msgpack_native(obj), ttl, if_not_set=if_not_set)


async def get(name: str, default: Any | None = None, data_type: Any = None) -> Any | None:
    if data := await get_bytes(name):
        return deserialize_msgpack_native(data, data_type)

    return default


async def store_string(name: str, data: str, ttl: int | None = None) -> None:
    await store_bytes(name, data.encode(), ttl)


async def get_string(name: str) -> str | None:
    if data := await get_bytes(name):
        return data.decode('utf-8')

    return None


@_graceful()
async def store_sequence(name: str, data: Sequence, ttl: int | None = None) -> None:
    if data:
        r = get_valkey_client()
        await r.rpush(name, *map(serialize_msgpack_native, data))

        if ttl:
            await r.expire(name, ttl)


@_graceful(default=())
async def _lrange(name: str) -> Sequence[bytes]:
    return await get_valkey_client().lrange(name, 0, -1)


async def get_sequence(name: str, _type: type = list) -> Sequence:
    return _type(map(deserialize_msgpack_native, await _lrange(name)))


@_graceful()
async def store_dict(name: str, data: Mapping, ttl: int | None = None) -> None:
    if data:
        r = get_valkey_client()
        await r.hset(name, mapping={str(k): serialize_msgpack_native(v) for k, v in data.items()})

        if ttl:
            await r.expire(name, ttl)


@_graceful()
async def get_dict(name: str, value_data_type=None) -> dict | None:
    if data := await get_valkey_client().hgetall(name):
        return {k.decode(): deserialize_msgpack_native(v, value_data_type) for k, v in data.items()}

    return None


@_graceful()
async def _hget(name: str, key: str) -> bytes | None:
    return await get_valkey_client().hget(name, key)


async def get_dict_item(name: str, key: str, data_type=None, default=None):
    if data := await _hget(name, key):
        return deserialize_msgpack_native(data, data_type)

    return default


@_graceful()
async def set_dict_item(name: str, key: str, obj: Any) -> None:
    await get_valkey_client().hset(name, key, serialize_msgpack_native(obj))


@_graceful()
async def delete_dict_item(name: str, *keys) -> None:
    await get_valkey_client().hdel(name, *keys)


@_graceful()
async def store_set(name: str, value: set, ttl: int | None = None) -> None:
    if value:
        r = get_valkey_client()
        await r.sadd(name, *map(serialize_msgpack_native, value))

        if ttl:
            await r.expire(name, ttl)


@_graceful(default=False)
async def has_set_item(name: str, value: str) -> bool:
    return await get_valkey_client().sismember(name, serialize_msgpack_native(value)) == 1


@_graceful()
async def add_set_item(name: str, *values: str) -> None:
    await get_valkey_client().sadd(name, *map(serialize_msgpack_native, values))


@_graceful()
async def delete_set_item(name: str, value: str) -> None:
    await get_valkey_client().srem(name, serialize_msgpack_native(value))


@_graceful()
async def get_set_members(name: str) -> set[str] | None:
    return set(map(deserialize_msgpack_native, await get_valkey_client().smembers(name)))
