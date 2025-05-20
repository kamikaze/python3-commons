import logging
import socket
from platform import platform
from typing import Any, Mapping, Sequence

import valkey
from pydantic import RedisDsn
from valkey.asyncio import ConnectionPool, Sentinel, StrictValkey, Valkey
from valkey.asyncio.retry import Retry
from valkey.backoff import FullJitterBackoff
from valkey.typing import ResponseT

from python3_commons.conf import valkey_settings
from python3_commons.helpers import SingletonMeta
from python3_commons.serializers.msgspec import (
    deserialize_msgpack,
    deserialize_msgpack_native,
    serialize_msgpack,
    serialize_msgpack_native,
)

logger = logging.getLogger(__name__)


class AsyncValkeyClient(metaclass=SingletonMeta):
    def __init__(self, dsn: RedisDsn, sentinel_dsn: RedisDsn | None):
        self._valkey_pool = None
        self._valkey = None

        if sentinel_dsn:
            self._initialize_sentinel(sentinel_dsn)
        else:
            self._initialize_standard_pool(dsn)

    @staticmethod
    def _get_keepalive_options():
        if platform == 'linux' or platform == 'darwin':
            return {socket.TCP_KEEPIDLE: 10, socket.TCP_KEEPINTVL: 5, socket.TCP_KEEPCNT: 5}
        else:
            return {}

    def _initialize_sentinel(self, dsn: RedisDsn):
        sentinel = Sentinel(
            [(dsn.host, dsn.port)],
            socket_connect_timeout=10,
            socket_timeout=60,
            password=dsn.password,
            sentinel_kwargs={'password': dsn.password},
        )

        ka_options = self._get_keepalive_options()

        self._valkey = sentinel.master_for(
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

    def _initialize_standard_pool(self, dsn: RedisDsn):
        self._valkey_pool = ConnectionPool.from_url(str(dsn))
        self._valkey = StrictValkey(connection_pool=self._valkey_pool)

    def get_client(self) -> Valkey:
        return self._valkey


def get_valkey_client() -> Valkey:
    return AsyncValkeyClient(valkey_settings.dsn, valkey_settings.sentinel_dsn).get_client()


async def scan(
    cursor: int = 0,
    match: bytes | str | memoryview | None = None,
    count: int | None = None,
    _type: str | None = None,
    **kwargs,
) -> ResponseT:
    return await get_valkey_client().scan(cursor, match, count, _type, **kwargs)


async def delete(*names: str | bytes | memoryview):
    await get_valkey_client().delete(*names)


async def store_bytes(name: str, data: bytes, ttl: int = None, if_not_set: bool = False):
    r = get_valkey_client()

    return await r.set(name, data, ex=ttl, nx=if_not_set)


async def get_bytes(name: str) -> bytes | None:
    r = get_valkey_client()

    return await r.get(name)


async def store(name: str, obj: Any, ttl: int = None, if_not_set: bool = False):
    return await store_bytes(name, serialize_msgpack_native(obj), ttl, if_not_set)


async def get(name: str, default=None, data_type: Any = None) -> Any:
    if data := await get_bytes(name):
        return deserialize_msgpack_native(data, data_type)

    return default


async def store_string(name: str, data: str, ttl: int = None):
    await store_bytes(name, data.encode(), ttl)


async def get_string(name: str) -> str | None:
    if data := await get_bytes(name):
        return data.decode('utf-8')

    return None


async def store_sequence(name: str, data: Sequence, ttl: int = None):
    if data:
        try:
            r = get_valkey_client()
            await r.rpush(name, *map(serialize_msgpack_native, data))

            if ttl:
                await r.expire(name, ttl)
        except valkey.exceptions.ConnectionError as e:
            logger.error(f'Failed to store sequence in cache: {e}')


async def get_sequence(name: str, _type: type = list) -> Sequence:
    r = get_valkey_client()
    lrange = await r.lrange(name, 0, -1)

    return _type(map(deserialize_msgpack_native, lrange))


async def store_dict(name: str, data: Mapping, ttl: int = None):
    if data:
        try:
            r = get_valkey_client()
            data = {k: serialize_msgpack_native(v) for k, v in data.items()}
            await r.hset(name, mapping=data)

            if ttl:
                await r.expire(name, ttl)
        except valkey.exceptions.ConnectionError as e:
            logger.error(f'Failed to store dict in cache: {e}')


async def get_dict(name: str, value_data_type=None) -> dict | None:
    r = get_valkey_client()

    if data := await r.hgetall(name):
        data = {k.decode(): deserialize_msgpack(v, value_data_type) for k, v in data.items()}

        return data

    return None


async def set_dict(name: str, mapping: dict, ttl: int = None):
    if mapping:
        try:
            r = get_valkey_client()
            mapping = {str(k): serialize_msgpack(v) for k, v in mapping.items()}
            await r.hset(name, mapping=mapping)

            if ttl:
                await r.expire(name, ttl)
        except valkey.exceptions.ConnectionError as e:
            logger.error(f'Failed to set dict in cache: {e}')


async def get_dict_item(name: str, key: str, data_type=None, default=None):
    try:
        r = get_valkey_client()

        if data := await r.hget(name, key):
            return deserialize_msgpack_native(data, data_type)

        return default
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to get dict item from cache: {e}')

    return None


async def set_dict_item(name: str, key: str, obj: Any):
    try:
        r = get_valkey_client()
        await r.hset(name, key, serialize_msgpack_native(obj))
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to set dict item in cache: {e}')


async def delete_dict_item(name: str, *keys):
    try:
        r = get_valkey_client()
        await r.hdel(name, *keys)
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to delete dict item from cache: {e}')


async def store_set(name: str, value: set, ttl: int = None):
    try:
        r = get_valkey_client()
        await r.sadd(name, *map(serialize_msgpack_native, value))

        if ttl:
            await r.expire(name, ttl)
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to store set in cache: {e}')


async def has_set_item(name: str, value: str) -> bool:
    try:
        r = get_valkey_client()

        return await r.sismember(name, serialize_msgpack_native(value)) == 1
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to check if set has item in cache: {e}')

    return False


async def add_set_item(name: str, *values: str):
    try:
        r = get_valkey_client()
        await r.sadd(name, *map(serialize_msgpack_native, values))
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to add set item into cache: {e}')


async def delete_set_item(name: str, value: str):
    r = get_valkey_client()
    await r.srem(name, serialize_msgpack_native(value))


async def get_set_members(name: str) -> set[str] | None:
    try:
        r = get_valkey_client()
        smembers = await r.smembers(name)

        return set(map(deserialize_msgpack_native, smembers))
    except valkey.exceptions.ConnectionError as e:
        logger.error(f'Failed to get set members from cache: {e}')

    return None


async def exists(name: str) -> bool:
    r = get_valkey_client()

    return await r.exists(name) == 1
