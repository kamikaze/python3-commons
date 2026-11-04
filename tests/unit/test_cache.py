import asyncio
import logging
import warnings
from datetime import timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import msgspec
import pytest
from pydantic import ValidationError
from valkey.asyncio import BlockingConnectionPool, SentinelConnectionPool
from valkey.exceptions import AuthenticationError as ValkeyAuthenticationError
from valkey.exceptions import BusyLoadingError as ValkeyBusyLoadingError
from valkey.exceptions import ConnectionError as ValkeyConnectionError
from valkey.exceptions import TimeoutError as ValkeyTimeoutError

from python3_commons import cache
from python3_commons.conf import ValkeySettings, get_valkey_settings
from python3_commons.serializers.msgspec import serialize_msgpack_native


class FakePipeline:
    """Models valkey's asyncio Pipeline: command methods queue synchronously, only execute() is awaited."""

    def __init__(self, *, existing: bool = False, execute_error: Exception | None = None) -> None:
        self.queued: list[tuple[Any, ...]] = []
        self.existing = existing
        self.execute_error = execute_error
        self.executed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> None:
        return None

    def _queue(self, command: str, *args, **kwargs):
        self.queued.append((command, args, kwargs))

        return self

    def exists(self, *args):
        return self._queue('exists', *args)

    def delete(self, *args):
        return self._queue('delete', *args)

    def rpush(self, *args):
        return self._queue('rpush', *args)

    def hset(self, *args, **kwargs):
        return self._queue('hset', *args, **kwargs)

    def sadd(self, *args):
        return self._queue('sadd', *args)

    def expire(self, *args):
        return self._queue('expire', *args)

    async def execute(self):
        if self.execute_error is not None:
            raise self.execute_error

        self.executed = True

        return [int(self.existing)] + [1] * (len(self.queued) - 1)


def _client_with_pipeline(mocker, pipeline: FakePipeline) -> MagicMock:
    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipeline)
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    return client


@pytest.fixture(autouse=True)
def _reset_replace_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, '_replace_warned', False)


@pytest.fixture(params=[ValkeyConnectionError, ValkeyTimeoutError])
def broken_client(request, mocker):
    client = MagicMock()
    client.pipeline = MagicMock(return_value=FakePipeline(execute_error=request.param('down')))

    methods = 'scan delete exists set get rpush lrange expire hset hgetall hget hdel sadd sismember srem smembers'

    for method in methods.split():
        setattr(client, method, AsyncMock(side_effect=request.param('down')))

    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('call', 'expected'),
    [
        (cache.scan, (0, ())),
        (lambda: cache.delete('k'), None),
        (lambda: cache.exists('k'), False),
        (lambda: cache.store_bytes('k', b'v'), None),
        (lambda: cache.get_bytes('k'), None),
        (lambda: cache.store('k', {'a': 1}), None),
        (lambda: cache.get('k', default='fallback'), 'fallback'),
        (lambda: cache.store_string('k', 'v'), None),
        (lambda: cache.get_string('k'), None),
        (lambda: cache.store_sequence('k', [1, 2]), None),
        (lambda: cache.get_sequence('k'), []),
        (lambda: cache.get_sequence('k', tuple), ()),
        (lambda: cache.store_dict('k', {'a': 1}), None),
        (lambda: cache.get_dict('k'), None),
        (lambda: cache.get_dict_item('k', 'a', default='fallback'), 'fallback'),
        (lambda: cache.set_dict_item('k', 'a', 1), None),
        (lambda: cache.delete_dict_item('k', 'a'), None),
        (lambda: cache.store_set('k', {'a'}), None),
        (lambda: cache.has_set_item('k', 'a'), False),
        (lambda: cache.add_set_item('k', 'a'), None),
        (lambda: cache.delete_set_item('k', 'a'), None),
        (lambda: cache.get_set_members('k'), None),
    ],
    ids=[
        'scan',
        'delete',
        'exists',
        'store_bytes',
        'get_bytes',
        'store',
        'get',
        'store_string',
        'get_string',
        'store_sequence',
        'get_sequence',
        'get_sequence_tuple',
        'store_dict',
        'get_dict',
        'get_dict_item',
        'set_dict_item',
        'delete_dict_item',
        'store_set',
        'has_set_item',
        'add_set_item',
        'delete_set_item',
        'get_set_members',
    ],
)
@pytest.mark.usefixtures('broken_client')
async def test_cache_degrades_gracefully_when_valkey_is_unavailable(call, expected, caplog) -> None:
    assert await call() == expected
    assert 'Cache operation' in caplog.text


@pytest.mark.asyncio
async def test_store_dict_and_get_dict_use_the_same_serializer(mocker) -> None:
    pipe = FakePipeline()
    client = _client_with_pipeline(mocker, pipe)

    await cache.store_dict('k', {'a': 1, 2: 'b'})

    stored = next(kwargs['mapping'] for command, _, kwargs in pipe.queued if command == 'hset')
    client.hgetall = AsyncMock(side_effect=lambda name: {k.encode(): v for k, v in stored.items()})

    assert await cache.get_dict('k') == {'a': 1, '2': 'b'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('call', 'command'),
    [
        (lambda: cache.store_sequence('k', [1, 2], 60), 'rpush'),
        (lambda: cache.store_dict('k', {'a': 1}, 60), 'hset'),
        (lambda: cache.store_set('k', {'a'}, 60), 'sadd'),
        (lambda: cache.append_sequence('k', 1, 2, ttl=60), 'rpush'),
        (lambda: cache.update_dict('k', {'a': 1}, 60), 'hset'),
    ],
    ids=['store_sequence', 'store_dict', 'store_set', 'append_sequence', 'update_dict'],
)
async def test_collection_write_and_expire_share_one_transaction(call, command, mocker) -> None:
    pipe = FakePipeline()
    client = _client_with_pipeline(mocker, pipe)

    await call()

    client.pipeline.assert_called_once_with(transaction=True)
    assert [q[0] for q in pipe.queued] == ['exists', command, 'expire']
    assert pipe.queued[-1][1] == ('k', 60)
    assert pipe.executed


@pytest.mark.asyncio
async def test_failed_transaction_degrades_and_nothing_is_written_outside_it(mocker) -> None:
    pipe = FakePipeline(execute_error=ValkeyTimeoutError('t/o'))
    client = _client_with_pipeline(mocker, pipe)

    await cache.store_sequence('k', [1], 60)

    assert not pipe.executed
    assert not client.method_calls[1:]


@pytest.mark.asyncio
async def test_collection_ttl_zero_warns_and_sets_no_expiry(mocker) -> None:
    pipe = FakePipeline()
    _client_with_pipeline(mocker, pipe)

    with pytest.warns(DeprecationWarning, match=r'ttl=0.*1\.0') as record:
        await cache.store_sequence('k', [1], 0)

    assert record[0].filename == __file__
    assert 'expire' not in [q[0] for q in pipe.queued]


@pytest.mark.asyncio
@pytest.mark.parametrize('ttl', [0, -5])
async def test_store_bytes_non_positive_ttl_warns_and_still_sends_it(ttl, mocker) -> None:
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    with pytest.warns(DeprecationWarning, match=r'ttl <= 0.*1\.0') as record:
        await cache.store_bytes('k', b'v', ttl)

    assert record[0].filename == __file__
    client.set.assert_awaited_once_with('k', b'v', ex=ttl, nx=False)


@pytest.mark.asyncio
async def test_store_with_non_positive_ttl_warns_once_pointing_at_the_caller(mocker) -> None:
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    with pytest.warns(DeprecationWarning, match='1.0') as record:
        await cache.store('k', 1, 0)

    assert len(record) == 1
    assert record[0].filename == __file__


@pytest.mark.asyncio
async def test_positive_ttl_and_timedelta_do_not_warn(mocker) -> None:
    from datetime import timedelta  # noqa: PLC0415

    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    _client_with_pipeline(mocker, FakePipeline())
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    await cache.store_bytes('k', b'v', 5)
    await cache.store_bytes('k', b'v', timedelta(seconds=5))
    await cache.store_bytes('k', b'v')


@pytest.mark.asyncio
async def test_rewrite_of_existing_key_warns_once_per_process(mocker) -> None:
    _client_with_pipeline(mocker, FakePipeline(existing=True))

    with pytest.warns(DeprecationWarning, match=r'replaces instead of merging.*1\.0') as record:
        await cache.store_sequence('k', [1])

    assert record[0].filename == __file__

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        _client_with_pipeline(mocker, FakePipeline(existing=True))
        await cache.store_dict('k', {'a': 1})
        await cache.store_set('k', {'a'})


@pytest.mark.asyncio
async def test_first_write_and_explicit_merge_api_do_not_warn(mocker) -> None:
    _client_with_pipeline(mocker, FakePipeline(existing=False))
    await cache.store_sequence('k', [1])

    _client_with_pipeline(mocker, FakePipeline(existing=True))
    await cache.append_sequence('k', 1)
    await cache.update_dict('k', {'a': 1})


@pytest.mark.asyncio
async def test_swallowed_authentication_error_warns(mocker) -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=ValkeyAuthenticationError('bad password'))
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    with pytest.warns(DeprecationWarning, match=r'AuthenticationError.*1\.0') as record:
        assert await cache.get_bytes('k') is None

    assert record[0].filename == __file__


@pytest.mark.asyncio
async def test_plain_connection_error_does_not_warn(mocker) -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=ValkeyConnectionError('down'))
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    assert await cache.get_bytes('k') is None


class Point(msgspec.Struct, frozen=True):
    x: int


@pytest.mark.asyncio
async def test_decode_errors_are_misses_with_a_warning(mocker, caplog) -> None:
    garbage = serialize_msgpack_native({'x': 'not an int'})
    client = MagicMock()
    client.get = AsyncMock(return_value=garbage)
    client.hget = AsyncMock(return_value=garbage)
    client.hgetall = AsyncMock(return_value={b'a': garbage})
    client.lrange = AsyncMock(return_value=[garbage])
    client.smembers = AsyncMock(return_value={garbage})
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    assert await cache.get('k', default='d', data_type=Point) == 'd'
    assert await cache.get_dict_item('k', 'a', Point, default='d') == 'd'
    assert await cache.get_dict('k', Point) is None
    assert await cache.get_sequence('k', data_type=Point) == []
    assert await cache.get_sequence('k', tuple, data_type=Point) == ()
    assert await cache.get_set_members('k', data_type=Point) is None
    assert caplog.text.count('could not be decoded') == 6


@pytest.mark.asyncio
async def test_collection_getters_accept_data_type(mocker) -> None:
    raw = serialize_msgpack_native({'x': 1})
    client = MagicMock()
    client.lrange = AsyncMock(return_value=[raw])
    client.smembers = AsyncMock(return_value={raw})
    client.hgetall = AsyncMock(return_value={b'a': raw})
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    assert await cache.get_sequence('k', data_type=Point) == [Point(1)]
    assert await cache.get_dict('k', Point) == {'a': Point(1)}
    assert await cache.get_set_members('k', Point) == {Point(1)}


@pytest.mark.asyncio
async def test_get_string_returns_empty_string(mocker) -> None:
    client = MagicMock()
    client.get = AsyncMock(return_value=b'')
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    assert await cache.get_string('k') == ''


@pytest.fixture
def valkey_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('VALKEY_DSN', 'redis://localhost:6379')
    monkeypatch.delenv('VALKEY_SENTINEL_DSN', raising=False)
    get_valkey_settings.cache_clear()
    monkeypatch.setattr(cache, '_clients', {})
    monkeypatch.setattr(cache, '_sentinels', {})


@pytest.mark.usefixtures('valkey_dsn')
def test_close_client_per_loop_leaves_no_entries() -> None:
    async def job() -> None:
        client = cache.get_valkey_client()
        assert cache.get_valkey_client() is client
        await cache.close_client()

    asyncio.run(job())
    asyncio.run(job())

    assert cache._clients == {}  # noqa: SLF001


@pytest.mark.usefixtures('valkey_dsn')
def test_closed_loops_are_evicted_on_lookup_without_close_client() -> None:
    loops: list[asyncio.AbstractEventLoop] = []

    async def job() -> None:
        loops.append(asyncio.get_running_loop())
        cache.get_valkey_client()

    for _ in range(3):
        asyncio.run(job())

    assert len(cache._clients) == 1  # noqa: SLF001
    assert list(cache._clients) == [loops[-1]]  # noqa: SLF001


@pytest.mark.asyncio
async def test_missing_dsn_raises_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('VALKEY_DSN', raising=False)
    monkeypatch.delenv('VALKEY_SENTINEL_DSN', raising=False)
    get_valkey_settings.cache_clear()
    monkeypatch.setattr(cache, '_clients', {})

    with pytest.raises(ValueError, match='VALKEY_DSN or VALKEY_SENTINEL_DSN'):
        cache.get_valkey_client()


@pytest.mark.asyncio
@pytest.mark.usefixtures('valkey_dsn')
async def test_async_valkey_client_alias_is_deprecated_and_returns_loop_client() -> None:
    with pytest.warns(DeprecationWarning, match='1.0'):
        legacy = cache.AsyncValkeyClient(get_valkey_settings().dsn, None)

    assert legacy.get_client() is cache.get_valkey_client()
    await cache.close_client()


# ---------------------------------------------------------------------------------------------------------------
# ValkeyCache (2.G2 a): the 1.0 contract, on a fake client


_CLIENT_COMMANDS = (
    *('scan', 'delete', 'exists', 'set', 'get', 'lrange', 'hgetall', 'hget'),
    *('hset', 'hdel', 'sadd', 'sismember', 'srem', 'smembers'),
)


def _fake_client(pipeline: FakePipeline | None = None) -> MagicMock:
    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipeline or FakePipeline())
    client.aclose = AsyncMock()

    for method in _CLIENT_COMMANDS:
        setattr(client, method, AsyncMock())

    return client


@pytest.fixture
def client() -> MagicMock:
    return _fake_client()


@pytest.fixture
def vcache(client: MagicMock) -> cache.ValkeyCache:
    return cache.ValkeyCache(client)


@pytest.mark.asyncio
async def test_store_methods_return_bool_and_delete_returns_int(vcache, client) -> None:
    client.set.return_value = True
    client.delete.return_value = 2

    assert await vcache.store_bytes('k', b'v', 60) is True
    assert await vcache.store('k', {'a': 1}, timedelta(seconds=5)) is True
    assert await vcache.store_string('k', 'v') is True
    assert await vcache.store_sequence('k', [1]) is True
    assert await vcache.append_sequence('k', 1) is True
    assert await vcache.store_dict('k', {'a': 1}) is True
    assert await vcache.update_dict('k', {'a': 1}) is True
    assert await vcache.store_set('k', {'a'}) is True
    assert await vcache.set_dict_item('k', 'a', 1) is True
    assert await vcache.add_set_item('k', 'a') is True
    assert await vcache.delete('a', 'b') == 2


@pytest.mark.asyncio
async def test_store_bytes_if_not_set_returns_false_when_the_key_exists(vcache, client) -> None:
    client.set.return_value = None

    assert await vcache.store_bytes('k', b'v', if_not_set=True) is False
    client.set.assert_awaited_once_with('k', b'v', ex=None, nx=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('call', 'command'),
    [
        (lambda c: c.store_sequence('k', [1, 2], 60), 'rpush'),
        (lambda c: c.store_dict('k', {'a': 1}, 60), 'hset'),
        (lambda c: c.store_set('k', {'a'}, 60), 'sadd'),
    ],
    ids=['sequence', 'dict', 'set'],
)
async def test_collection_stores_replace_in_one_transaction(call, command, client) -> None:
    pipe = FakePipeline()
    client.pipeline.return_value = pipe

    assert await call(cache.ValkeyCache(client)) is True

    client.pipeline.assert_called_once_with(transaction=True)
    assert [q[0] for q in pipe.queued] == ['delete', command, 'expire']
    assert pipe.queued[0][1] == ('k',)
    assert pipe.queued[-1][1] == ('k', 60)
    assert pipe.executed


@pytest.mark.asyncio
async def test_collection_store_without_ttl_sets_no_expiry(vcache, client) -> None:
    pipe = FakePipeline()
    client.pipeline.return_value = pipe

    await vcache.store_sequence('k', [1])

    assert [q[0] for q in pipe.queued] == ['delete', 'rpush']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'call',
    [
        lambda c: c.store_sequence('k', []),
        lambda c: c.store_dict('k', {}),
        lambda c: c.store_set('k', set()),
    ],
    ids=['sequence', 'dict', 'set'],
)
async def test_storing_an_empty_collection_deletes_the_key(call, client) -> None:
    pipe = FakePipeline()
    client.pipeline.return_value = pipe

    assert await call(cache.ValkeyCache(client)) is True

    assert [q[0] for q in pipe.queued] == ['delete']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('call', 'command'),
    [
        (lambda c: c.append_sequence('k', 1, 2, ttl=60), 'rpush'),
        (lambda c: c.update_dict('k', {'a': 1}, 60), 'hset'),
    ],
    ids=['append_sequence', 'update_dict'],
)
async def test_merge_methods_do_not_delete_first(call, command, client) -> None:
    pipe = FakePipeline()
    client.pipeline.return_value = pipe

    assert await call(cache.ValkeyCache(client)) is True

    assert [q[0] for q in pipe.queued] == [command, 'expire']


@pytest.mark.asyncio
async def test_merge_methods_with_nothing_to_merge_touch_nothing(vcache, client) -> None:
    assert await vcache.append_sequence('k') is True
    assert await vcache.update_dict('k', {}) is True

    client.pipeline.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('ttl', [0, -1, timedelta(0), timedelta(seconds=-3)])
async def test_non_positive_ttl_raises_value_error_before_contacting_the_server(ttl, vcache, client) -> None:
    for call in (
        lambda: vcache.store_bytes('k', b'v', ttl),
        lambda: vcache.store('k', 1, ttl),
        lambda: vcache.store_string('k', 's', ttl),
        lambda: vcache.store_sequence('k', [1], ttl),
        lambda: vcache.store_sequence('k', [], ttl),
        lambda: vcache.append_sequence('k', 1, ttl=ttl),
        lambda: vcache.store_dict('k', {'a': 1}, ttl),
        lambda: vcache.update_dict('k', {'a': 1}, ttl),
        lambda: vcache.store_set('k', {'a'}, ttl),
    ):
        with pytest.raises(ValueError, match='ttl must be positive'):
            await call()

    client.set.assert_not_called()
    client.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_non_positive_ttl_is_not_swallowed_by_fail_open(client) -> None:
    with pytest.raises(ValueError, match='ttl'):
        await cache.ValkeyCache(client, fail_open=True).store_bytes('k', b'v', 0)


TRANSIENT = [ValkeyConnectionError, ValkeyTimeoutError, ValkeyBusyLoadingError]


def _break(client: MagicMock, error: Exception) -> None:
    client.pipeline.return_value = FakePipeline(execute_error=error)

    for method in _CLIENT_COMMANDS:
        getattr(client, method).side_effect = error


@pytest.mark.asyncio
@pytest.mark.parametrize('error', TRANSIENT)
@pytest.mark.parametrize(
    ('call', 'expected'),
    [
        (lambda c: c.scan(), (0, ())),
        (lambda c: c.delete('k'), 0),
        (lambda c: c.exists('k'), False),
        (lambda c: c.store_bytes('k', b'v'), False),
        (lambda c: c.get_bytes('k'), None),
        (lambda c: c.store('k', 1), False),
        (lambda c: c.get('k', default='d'), 'd'),
        (lambda c: c.store_string('k', 's'), False),
        (lambda c: c.get_string('k'), None),
        (lambda c: c.store_sequence('k', [1]), False),
        (lambda c: c.append_sequence('k', 1), False),
        (lambda c: c.get_sequence('k'), []),
        (lambda c: c.get_sequence('k', tuple), ()),
        (lambda c: c.store_dict('k', {'a': 1}), False),
        (lambda c: c.update_dict('k', {'a': 1}), False),
        (lambda c: c.get_dict('k'), None),
        (lambda c: c.get_dict_item('k', 'a', default='d'), 'd'),
        (lambda c: c.set_dict_item('k', 'a', 1), False),
        (lambda c: c.delete_dict_item('k', 'a'), 0),
        (lambda c: c.store_set('k', {'a'}), False),
        (lambda c: c.has_set_item('k', 'a'), False),
        (lambda c: c.add_set_item('k', 'a'), False),
        (lambda c: c.delete_set_item('k', 'a'), 0),
        (lambda c: c.get_set_members('k'), None),
    ],
    ids=[
        'scan',
        'delete',
        'exists',
        'store_bytes',
        'get_bytes',
        'store',
        'get',
        'store_string',
        'get_string',
        'store_sequence',
        'append_sequence',
        'get_sequence',
        'get_sequence_tuple',
        'store_dict',
        'update_dict',
        'get_dict',
        'get_dict_item',
        'set_dict_item',
        'delete_dict_item',
        'store_set',
        'has_set_item',
        'add_set_item',
        'delete_set_item',
        'get_set_members',
    ],
)
async def test_fail_open_swallows_transient_errors(call, expected, error, client) -> None:
    _break(client, error('down'))

    assert await call(cache.ValkeyCache(client, fail_open=True)) == expected


@pytest.mark.asyncio
async def test_fail_open_is_the_default(client) -> None:
    _break(client, ValkeyConnectionError('down'))

    assert await cache.ValkeyCache(client).get_bytes('k') is None


@pytest.mark.asyncio
async def test_authentication_error_is_never_swallowed(client) -> None:
    _break(client, ValkeyAuthenticationError('bad password'))

    vcache = cache.ValkeyCache(client, fail_open=True)

    with pytest.raises(ValkeyAuthenticationError):
        await vcache.get_bytes('k')

    with pytest.raises(ValkeyAuthenticationError):
        await vcache.store_sequence('k', [1])


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [*TRANSIENT, ValkeyAuthenticationError])
async def test_fail_closed_raises_everything(error, client) -> None:
    _break(client, error('down'))

    with pytest.raises(error):
        await cache.ValkeyCache(client, fail_open=False).get_bytes('k')


@pytest.mark.asyncio
async def test_other_errors_are_not_swallowed(client) -> None:
    client.get.side_effect = RuntimeError('bug')

    with pytest.raises(RuntimeError, match='bug'):
        await cache.ValkeyCache(client).get_bytes('k')


@pytest.mark.asyncio
async def test_first_failure_logs_a_warning_with_traceback_and_later_ones_are_rate_limited(
    client, caplog, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(cache.time, 'monotonic', lambda: now[0])
    _break(client, ValkeyConnectionError('down'))
    vcache = cache.ValkeyCache(client)

    with caplog.at_level(logging.WARNING, logger='python3_commons.cache'):
        await vcache.get_bytes('k')

        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert caplog.records[0].exc_info is not None
        assert 'get_bytes' in caplog.records[0].getMessage()

        for _ in range(5):
            now[0] += 1
            await vcache.exists('k')

        assert len(caplog.records) == 1

        now[0] += cache._FAILURE_LOG_INTERVAL  # noqa: SLF001
        await vcache.get_bytes('k')

    assert len(caplog.records) == 2
    assert '5 failures since the last warning' not in caplog.records[1].getMessage()
    assert '6 failures since the last warning' in caplog.records[1].getMessage()
    assert caplog.records[1].exc_info is None


@pytest.mark.asyncio
async def test_failure_logs_are_per_cache(caplog) -> None:
    caches = []

    for _ in range(2):
        broken = _fake_client()
        _break(broken, ValkeyTimeoutError('t/o'))
        caches.append(cache.ValkeyCache(broken))

    with caplog.at_level(logging.WARNING, logger='python3_commons.cache'):
        for vcache in caches:
            await vcache.get_bytes('k')

    assert len(caplog.records) == 2


@pytest.mark.asyncio
async def test_failed_transaction_is_swallowed_as_false(client) -> None:
    pipe = FakePipeline(execute_error=ValkeyTimeoutError('t/o'))
    client.pipeline.return_value = pipe

    assert await cache.ValkeyCache(client).store_sequence('k', [1], 60) is False
    assert not pipe.executed


@pytest.mark.asyncio
async def test_getters_decode_with_data_type_and_treat_decode_errors_as_misses(vcache, client, caplog) -> None:
    good = serialize_msgpack_native({'x': 1})
    bad = serialize_msgpack_native({'x': 'no'})
    client.get.return_value = good
    client.lrange.return_value = [good]
    client.hgetall.return_value = {b'a': good}
    client.hget.return_value = good
    client.smembers.return_value = {good}

    assert await vcache.get('k', data_type=Point) == Point(1)
    assert await vcache.get_sequence('k', data_type=Point) == [Point(1)]
    assert await vcache.get_sequence('k', tuple, data_type=Point) == (Point(1),)
    assert await vcache.get_sequence('k', container=tuple, data_type=Point) == (Point(1),)
    assert await vcache.get_dict('k', Point) == {'a': Point(1)}
    assert await vcache.get_dict_item('k', 'a', Point) == Point(1)
    assert await vcache.get_set_members('k', Point) == {Point(1)}

    client.get.return_value = bad
    client.lrange.return_value = [bad]
    client.hgetall.return_value = {b'a': bad}
    client.hget.return_value = bad
    client.smembers.return_value = {bad}

    assert await vcache.get('k', 'd', Point) == 'd'
    assert await vcache.get_sequence('k', data_type=Point) == []
    assert await vcache.get_dict('k', Point) is None
    assert await vcache.get_dict_item('k', 'a', Point, default='d') == 'd'
    assert await vcache.get_set_members('k', Point) is None
    assert caplog.text.count('could not be decoded') == 5


@pytest.mark.asyncio
async def test_get_string_returns_empty_string_and_missing_is_none(vcache, client) -> None:
    client.get.return_value = b''
    assert await vcache.get_string('k') == ''

    client.get.return_value = None
    assert await vcache.get_string('k') is None


@pytest.mark.asyncio
async def test_exists_has_set_item_and_scan_pass_through(vcache, client) -> None:
    client.exists.return_value = 1
    client.sismember.return_value = 1
    client.scan.return_value = (0, [b'a'])

    assert await vcache.exists('k') is True
    assert await vcache.has_set_item('k', 'a') is True
    assert await vcache.scan(0, 'a*', 10, 'string') == (0, [b'a'])
    client.scan.assert_awaited_once_with(0, 'a*', 10, 'string')


# lifecycle


@pytest.mark.asyncio
async def test_a_borrowed_client_is_not_closed(client) -> None:
    vcache = cache.ValkeyCache(client)
    await vcache.aclose()

    client.aclose.assert_not_awaited()
    assert vcache.client is client


@pytest.mark.asyncio
async def test_an_owned_client_is_closed_by_aclose_and_async_with(client) -> None:
    async with cache.ValkeyCache(client, owns_client=True) as vcache:
        assert isinstance(vcache, cache.ValkeyCache)
        client.aclose.assert_not_awaited()

    client.aclose.assert_awaited_once()


def _dsn_settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> ValkeySettings:
    for name, value in env.items():
        monkeypatch.setenv(f'VALKEY_{name}', value)

    return ValkeySettings()


@pytest.mark.asyncio
async def test_from_settings_builds_an_owned_client_with_one_set_of_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(monkeypatch, DSN='redis://:secret@valkey.example:6380/2')

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        pool = vcache.client.connection_pool
        kwargs = pool.connection_kwargs

        assert vcache.fail_open is True
        assert (kwargs['host'], kwargs['port'], kwargs['db'], kwargs['password']) == (
            'valkey.example',
            6380,
            2,
            'secret',
        )
        assert kwargs['socket_timeout'] == 60
        assert kwargs['socket_connect_timeout'] == 10
        assert kwargs['health_check_interval'] == 30
        assert kwargs['socket_keepalive'] is True
        assert kwargs['retry'] is not None
        assert not isinstance(pool, BlockingConnectionPool)
        assert vcache.client.auto_close_connection_pool is True


@pytest.mark.asyncio
async def test_from_settings_defaults_to_get_valkey_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('VALKEY_DSN', 'redis://valkey.example:6379')
    get_valkey_settings.cache_clear()

    async with cache.ValkeyCache.from_settings(fail_open=False) as vcache:
        assert vcache.client.connection_pool.connection_kwargs['host'] == 'valkey.example'
        assert vcache.fail_open is False


@pytest.mark.asyncio
async def test_settings_override_the_connection_options(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(
        monkeypatch,
        DSN='redis://valkey.example',
        SOCKET_TIMEOUT='5',
        SOCKET_CONNECT_TIMEOUT='2.5',
        HEALTH_CHECK_INTERVAL='0',
        RETRIES='0',
    )

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        kwargs = vcache.client.connection_pool.connection_kwargs

        assert (kwargs['socket_timeout'], kwargs['socket_connect_timeout']) == (5, 2.5)
        assert kwargs['health_check_interval'] == 0
        assert 'retry' not in kwargs


@pytest.mark.asyncio
async def test_a_bounded_pool_is_a_blocking_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(monkeypatch, DSN='redis://valkey.example', MAX_CONNECTIONS='7', POOL_TIMEOUT='3')

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        pool = vcache.client.connection_pool

        assert isinstance(pool, BlockingConnectionPool)
        assert pool.max_connections == 7
        assert pool.timeout == 3
        assert pool.connection_kwargs['socket_timeout'] == 60
        assert vcache.client.auto_close_connection_pool is True


@pytest.mark.asyncio
async def test_sentinel_mode_uses_the_same_kwargs_nodes_and_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(
        monkeypatch,
        SENTINEL_DSN='redis://:sentinel-pw@s1.example:26380',
        SENTINELS='s2.example:26381, s3.example',
        SENTINEL_SERVICE_NAME='primary-x',
        DSN='redis://ignored.example',
    )

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        pool = vcache.client.connection_pool
        assert vcache._sentinel is not None  # noqa: SLF001
        nodes = [node.connection_pool.connection_kwargs for node in vcache._sentinel.sentinels]  # noqa: SLF001

        assert cast('SentinelConnectionPool', pool).service_name == 'primary-x'
        assert [(n['host'], n['port']) for n in nodes] == [
            ('s1.example', 26380),
            ('s2.example', 26381),
            ('s3.example', 26379),
        ]
        assert {n['password'] for n in nodes} == {'sentinel-pw'}
        assert {n['socket_timeout'] for n in nodes} == {60}
        assert pool.connection_kwargs['password'] == 'sentinel-pw'
        assert pool.connection_kwargs['socket_timeout'] == 60
        assert pool.connection_kwargs['socket_connect_timeout'] == 10
        assert pool.connection_kwargs['health_check_interval'] == 30
        assert pool.connection_kwargs['retry'] is not None

    assert vcache._sentinel is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_sentinel_password_overrides_the_dsn_password_for_sentinel_nodes_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _dsn_settings(monkeypatch, SENTINEL_DSN='redis://:master-pw@s1.example', SENTINEL_PASSWORD='node-pw')

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        assert vcache._sentinel is not None  # noqa: SLF001
        nodes = vcache._sentinel.sentinels  # noqa: SLF001

        assert {n.connection_pool.connection_kwargs['password'] for n in nodes} == {'node-pw'}
        assert vcache.client.connection_pool.connection_kwargs['password'] == 'master-pw'


@pytest.mark.asyncio
async def test_a_bounded_sentinel_pool_is_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(monkeypatch, SENTINEL_DSN='redis://s1.example', MAX_CONNECTIONS='4', POOL_TIMEOUT='2')

    async with cache.ValkeyCache.from_settings(settings) as vcache:
        pool = vcache.client.connection_pool

        assert isinstance(pool, BlockingConnectionPool)
        assert (pool.max_connections, pool.timeout) == (4, 2)
        assert cast('SentinelConnectionPool', pool).is_master is True


@pytest.mark.asyncio
async def test_aclose_closes_sentinel_nodes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(monkeypatch, SENTINEL_DSN='redis://s1.example', SENTINELS='s2.example')
    vcache = cache.ValkeyCache.from_settings(settings)
    assert vcache._sentinel is not None  # noqa: SLF001
    nodes = vcache._sentinel.sentinels  # noqa: SLF001
    closers = [AsyncMock() for _ in nodes]

    for node, closer in zip(nodes, closers, strict=True):
        node.aclose = closer

    await vcache.aclose()
    await vcache.aclose()

    for closer in closers:
        closer.assert_awaited_once()


def test_no_dsn_raises_runtime_error_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match='VALKEY_DSN or VALKEY_SENTINEL_DSN'):
        cache.ValkeyCache.from_settings(ValkeySettings())

    get_valkey_settings.cache_clear()

    with pytest.raises(RuntimeError, match='VALKEY_DSN or VALKEY_SENTINEL_DSN'):
        cache.ValkeyCache.from_settings()


def test_sentinels_without_a_sentinel_dsn_is_a_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(monkeypatch, DSN='redis://valkey.example', SENTINELS='s2.example')

    with pytest.raises(RuntimeError, match='VALKEY_SENTINEL_DSN'):
        cache.ValkeyCache.from_settings(settings)


def test_valkey_settings_defaults_are_the_current_values() -> None:
    settings = ValkeySettings()

    assert settings.sentinels is None
    assert settings.sentinel_service_name == 'myprimary'
    assert settings.sentinel_password is None
    assert settings.sentinel_nodes == []
    assert (settings.socket_timeout, settings.socket_connect_timeout) == (60, 10)
    assert (settings.health_check_interval, settings.retries) == (30, 5)
    assert settings.max_connections is None


def test_valkey_settings_parse_sentinel_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _dsn_settings(
        monkeypatch, SENTINEL_DSN='redis://s1.example:1', SENTINELS='s2:2, [::1]:3 ,s4', SENTINEL_PASSWORD='pw'
    )

    assert settings.sentinel_nodes == [('s1.example', 1), ('s2', 2), ('::1', 3), ('s4', 26379)]
    assert 'pw' not in repr(settings)


@pytest.mark.parametrize(
    ('name', 'value'),
    [
        ('SENTINELS', 's2:notaport'),
        ('SENTINELS', 's2:70000'),
        ('SENTINELS', ':26379'),
        ('SOCKET_TIMEOUT', '0'),
        ('SOCKET_CONNECT_TIMEOUT', '-1'),
        ('MAX_CONNECTIONS', '0'),
        ('RETRIES', '-1'),
        ('POOL_TIMEOUT', '0'),
    ],
)
def test_valkey_settings_reject_invalid_values(name, value, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(f'VALKEY_{name}', value)

    with pytest.raises(ValidationError):
        ValkeySettings()


# ---------------------------------------------------------------------------------------------------------------
# 2.G2 (b): the per-loop default cache, __all__ and the get_sequence keyword


@pytest.fixture
def default_registry(monkeypatch: pytest.MonkeyPatch) -> dict[Any, Any]:
    registry: dict[Any, Any] = {}
    monkeypatch.setattr(cache, '_default_caches', registry)
    monkeypatch.setenv('VALKEY_DSN', 'redis://valkey.example:6379')
    get_valkey_settings.cache_clear()

    return registry


@pytest.mark.asyncio
async def test_default_cache_is_one_per_loop_and_built_from_the_settings(default_registry) -> None:
    default = cache.get_default_cache()

    assert cache.get_default_cache() is default
    assert default.fail_open is True
    assert default.client.connection_pool.connection_kwargs['host'] == 'valkey.example'
    assert default_registry == {asyncio.get_running_loop(): default}

    await cache.close_default_cache()


def test_a_second_loop_gets_a_new_cache_and_the_stale_entry_is_evicted(default_registry) -> None:
    seen: list[tuple[asyncio.AbstractEventLoop, cache.ValkeyCache]] = []

    async def job() -> None:
        seen.append((asyncio.get_running_loop(), cache.get_default_cache()))

    asyncio.run(job())

    assert len(default_registry) == 1  # the first loop is closed but nothing evicted it yet

    asyncio.run(job())

    assert seen[0][1] is not seen[1][1]
    assert list(default_registry) == [seen[1][0]]
    assert seen[0][0].is_closed()


def test_close_default_cache_empties_the_registry(default_registry) -> None:
    async def first() -> cache.ValkeyCache:
        default = cache.get_default_cache()
        default.aclose = AsyncMock()
        await cache.close_default_cache()
        default.aclose.assert_awaited_once()

        return default

    async def second() -> None:
        cache.get_default_cache()
        await cache.close_default_cache()
        await cache.close_default_cache()

    asyncio.run(first())
    assert default_registry == {}

    asyncio.run(second())
    assert default_registry == {}


def test_close_default_cache_also_drops_entries_of_closed_loops(default_registry) -> None:
    async def leave_open() -> None:
        cache.get_default_cache()

    async def close() -> None:
        await cache.close_default_cache()

    asyncio.run(leave_open())
    assert len(default_registry) == 1

    asyncio.run(close())
    assert default_registry == {}


@pytest.mark.asyncio
async def test_set_default_cache_replaces_and_returns_the_previous_one(default_registry, client) -> None:
    custom = cache.ValkeyCache(client, fail_open=False)

    assert cache.set_default_cache(custom) is None
    assert cache.get_default_cache() is custom

    replacement = cache.ValkeyCache(client)

    assert cache.set_default_cache(replacement) is custom
    assert default_registry == {asyncio.get_running_loop(): replacement}

    await cache.close_default_cache()

    client.aclose.assert_not_awaited()  # a borrowed client stays open
    assert default_registry == {}


@pytest.mark.asyncio
async def test_default_cache_without_a_dsn_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch, default_registry
) -> None:
    monkeypatch.delenv('VALKEY_DSN')
    get_valkey_settings.cache_clear()

    with pytest.raises(RuntimeError, match='VALKEY_DSN or VALKEY_SENTINEL_DSN'):
        cache.get_default_cache()

    assert default_registry == {}


def test_default_cache_registry_helpers_need_a_running_loop() -> None:
    with pytest.raises(RuntimeError, match='no running event loop'):
        cache.get_default_cache()


def test_all_lists_exactly_the_public_names() -> None:
    public = {
        name
        for name, obj in vars(cache).items()
        if not name.startswith('_') and getattr(obj, '__module__', None) == cache.__name__
    }

    assert set(cache.__all__) == public
    assert len(cache.__all__) == len(set(cache.__all__))


@pytest.mark.asyncio
async def test_get_sequence_type_keyword_is_deprecated(mocker) -> None:
    client = MagicMock()
    client.lrange = AsyncMock(return_value=[serialize_msgpack_native(1), serialize_msgpack_native(2)])
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    with pytest.warns(DeprecationWarning, match=r'_type.*container.*1\.0') as record:
        assert await cache.get_sequence('k', _type=tuple) == (1, 2)

    assert len(record) == 1
    assert record[0].filename == __file__


@pytest.mark.asyncio
async def test_get_sequence_container_keyword_and_position_do_not_warn(mocker) -> None:
    client = MagicMock()
    client.lrange = AsyncMock(return_value=[serialize_msgpack_native(1)])
    mocker.patch('python3_commons.cache.get_valkey_client', return_value=client)

    assert await cache.get_sequence('k', container=tuple) == (1,)
    assert await cache.get_sequence('k', tuple) == (1,)
    assert await cache.get_sequence('k') == [1]
