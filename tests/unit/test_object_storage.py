import asyncio
import contextlib
import importlib
import io
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from object_storage_client import ObjectStorageClient

from python3_commons import _paths, object_storage
from python3_commons.object_storage import ObjectStorageStream


class FakeListStream:
    def __init__(self, batches):
        self._batches = list(batches)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._batches:
            raise StopAsyncIteration

        return self._batches.pop(0)

    async def aclose(self):
        self.closed = True


def _listing_client(mocker, *batches, metadata: Any = None):
    """A client whose listing yields `batches`; `metadata` is a callable (async or not) or a dict by URL."""
    client = mocker.MagicMock(spec=ObjectStorageClient)
    client.streams = []

    async def list_objects_stream(url):
        stream = FakeListStream(batches)
        client.streams.append(stream)

        return stream

    async def get_object_metadata(url):
        result = metadata(url) if callable(metadata) else metadata[url]

        return await result if asyncio.iscoroutine(result) else result

    client.list_objects_stream = AsyncMock(side_effect=list_objects_stream)
    client.get_object_metadata = AsyncMock(side_effect=get_object_metadata)
    mocker.patch('python3_commons.object_storage.get_client', return_value=client)

    return client


def _meta(etag='E', size=1):
    return {'last_modified': '2026-01-02T03:04:05+00:00', 'size_bytes': size, 'e_tag': etag}


def test_build_url():
    # S3 schemes
    assert object_storage.build_url('s3://my-bucket', 'file.txt') == 's3://my-bucket/file.txt'
    assert object_storage.build_url('s3://my-bucket/prefix', 'file.txt') == 's3://my-bucket/prefix/file.txt'
    # Deduplication of prefix (deprecated, behaviour unchanged)
    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('s3://my-bucket/prefix', 'prefix/file.txt') == 's3://my-bucket/prefix/file.txt'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert (
            object_storage.build_url('s3://my-bucket/prefix', 's3://my-bucket/prefix/file.txt')
            == 's3://my-bucket/prefix/file.txt'
        )

    # Deduplication with bucket in path
    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('s3://my-bucket', 'my-bucket/file.txt') == 's3://my-bucket/file.txt'

    # Local paths
    assert object_storage.build_url('file:///var/data', 'file.txt') == 'file:///var/data/file.txt'
    assert object_storage.build_url('/var/data', 'file.txt') == 'file:///var/data/file.txt'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('/var/data', 'var/data/file.txt') == 'file:///var/data/file.txt'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('file:///var/data', 'var/data/file.txt') == 'file:///var/data/file.txt'

    # Empty path
    assert object_storage.build_url('s3://my-bucket', '') == 's3://my-bucket'


def test_get_client_and_singleton():
    client1 = object_storage.get_client()
    client2 = object_storage.get_client()
    assert isinstance(client1, ObjectStorageClient)
    assert client1 is client2


@pytest.mark.asyncio
async def test_object_storage_lifecycle(tmp_path):
    work_path = f'file://{tmp_path}'

    # 1. put_object with BytesIO
    content = b'Hello Object Storage!'
    url = await object_storage.put_object(work_path, 'test1.txt', io.BytesIO(content))
    assert url == f'{work_path}/test1.txt'

    # 2. put_object with bytes
    await object_storage.put_object(work_path, 'sub/test2.txt', b'Nested content')

    # 3. get_object
    retrieved = await object_storage.get_object(work_path, 'test1.txt')
    assert retrieved == content

    # 4. get_object_stream
    async with object_storage.get_object_stream(work_path, 'test1.txt') as stream:
        read_data = await stream.read()
        assert read_data == content

    async with object_storage.get_object_stream(work_path, 'test1.txt') as stream:
        chunks = [c async for c in stream]
        assert chunks == [content]

    async with object_storage.get_object_stream(work_path, 'test1.txt') as stream:
        next_chunk = await stream.next()
        assert next_chunk == content

    # 5. list_objects (recursive and non-recursive)
    listing = [obj async for obj in object_storage.list_objects(work_path)]
    keys = [item['Key'] for item in listing]
    assert 'test1.txt' in keys
    assert 'sub/test2.txt' in keys
    for item in listing:
        assert isinstance(item['LastModified'], datetime)

    non_rec = [obj async for obj in object_storage.list_objects(work_path, recursive=False)]
    non_rec_keys = [item['Key'] for item in non_rec]
    assert 'test1.txt' in non_rec_keys
    assert 'sub/test2.txt' not in non_rec_keys

    # 6. get_object_streams
    streams = [item async for item in object_storage.get_object_streams(work_path)]
    stream_keys = [item[0] for item in streams]
    assert 'test1.txt' in stream_keys
    assert 'sub/test2.txt' in stream_keys

    # 7. get_objects
    objects = [item async for item in object_storage.get_objects(work_path)]
    object_dict = {item[0]: item[2] for item in objects}
    assert object_dict['test1.txt'] == content
    assert object_dict['sub/test2.txt'] == b'Nested content'

    # 8. remove_object
    await object_storage.remove_object(work_path, 'test1.txt')
    remaining = [obj['Key'] async for obj in object_storage.list_objects(work_path)]
    assert 'test1.txt' not in remaining
    assert 'sub/test2.txt' in remaining

    # 9. remove_objects with prefix
    await object_storage.remove_objects(work_path, prefix='sub/')
    after_prefix_delete = [obj['Key'] async for obj in object_storage.list_objects(work_path)]
    assert 'sub/test2.txt' not in after_prefix_delete

    # 10. remove_objects with object_names
    await object_storage.put_object(work_path, 'test3.txt', b'content3')
    await object_storage.remove_objects(work_path, object_names=['test3.txt'])
    after_names_delete = [obj['Key'] async for obj in object_storage.list_objects(work_path)]
    assert 'test3.txt' not in after_names_delete

    # 11. remove_objects with empty args returns None
    assert await object_storage.remove_objects(work_path) is None


@pytest.mark.asyncio
async def test_object_storage_stream_multichunk():
    class MockStream:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def next(self):
            if self.chunks:
                return self.chunks.pop(0)
            return None

    # Test read() concatenating multiple chunks
    stream = ObjectStorageStream(cast('Any', MockStream([b'chunk1', b'chunk2', None])))
    assert await stream.read() == b'chunk1chunk2'

    # Test read() on empty stream
    empty_stream = ObjectStorageStream(cast('Any', MockStream([None])))
    assert await empty_stream.read() == b''

    # Test StopAsyncIteration on empty/None
    stream2 = ObjectStorageStream(cast('Any', MockStream([None])))
    with pytest.raises(StopAsyncIteration):
        await stream2.__anext__()


def test_build_url_additional_schemes_and_deduplication():
    assert object_storage.build_url('gs://my-bucket', 'file.txt') == 'gs://my-bucket/file.txt'
    assert object_storage.build_url('az://my-bucket', 'file.txt') == 'az://my-bucket/file.txt'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('s3://my-bucket/sub', 'sub/file.txt') == 's3://my-bucket/sub/file.txt'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('s3://my-bucket', 'gs://other-bucket/file.txt') == 'gs://other-bucket/file.txt'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert object_storage.build_url('s3://my-bucket', 'az://other-bucket/file.txt') == 'az://other-bucket/file.txt'

    assert object_storage.build_url('s3://my-bucket/') == 's3://my-bucket'


def test_build_url_schemeless_work_path_is_local_file_url(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    assert object_storage.build_url('my-bucket', 'file.txt') == f'file://{tmp_path}/my-bucket/file.txt'
    assert object_storage.build_url('/abs//dir/', 'a/b.txt') == 'file:///abs/dir/a/b.txt'
    assert object_storage.build_url('my-bucket') == f'file://{tmp_path}/my-bucket'


@pytest.mark.parametrize('work_path', ['', '   ', '/', 's3://'])
def test_build_url_rejects_empty_work_path(work_path):
    with pytest.raises(ValueError, match='empty'):
        object_storage.build_url(work_path, 'file.txt')


@pytest.mark.parametrize(
    ('work_path', 'expected'),
    [
        ('https://host:8443/p', 'https://host:8443/p/k.txt'),
        ('http://[::1]:9000/p/', 'http://[::1]:9000/p/k.txt'),
        ('gcs://b/p', 'gcs://b/p/k.txt'),
        ('abfss://c@acct.dfs.core.windows.net/p', 'abfss://c@acct.dfs.core.windows.net/p/k.txt'),
    ],
)
@pytest.mark.asyncio
async def test_module_functions_build_urls_for_every_scheme(mocker, work_path, expected):
    client = mocker.MagicMock(spec=ObjectStorageClient)
    client.put_object = AsyncMock()
    client.get_object = AsyncMock(return_value=b'x')
    mocker.patch.object(object_storage, 'get_client', return_value=client)

    await object_storage.put_object(work_path, 'k.txt', io.BytesIO(b'x'))
    await object_storage.get_object(work_path, 'k.txt')

    assert client.put_object.call_args[0][0] == expected
    assert client.get_object.call_args[0][0] == expected


@pytest.mark.asyncio
async def test_module_function_schemeless_work_path_is_local(mocker, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = mocker.MagicMock(spec=ObjectStorageClient)
    client.get_object = AsyncMock(return_value=b'x')
    mocker.patch.object(object_storage, 'get_client', return_value=client)

    await object_storage.get_object('bucket:prefix', 'k.txt')

    assert client.get_object.call_args[0][0] == f'file://{tmp_path}/bucket:prefix/k.txt'


@pytest.mark.asyncio
async def test_absolute_https_key_is_absolute_and_deprecated(mocker):
    client = mocker.MagicMock(spec=ObjectStorageClient)
    client.get_object = AsyncMock(return_value=b'x')
    mocker.patch.object(object_storage, 'get_client', return_value=client)

    with pytest.warns(DeprecationWarning, match='1.0') as record:
        await object_storage.get_object('s3://my-bucket', 'https://host:8443/p/k.txt')

    assert record[0].filename == __file__
    assert client.get_object.call_args[0][0] == 'https://host:8443/p/k.txt'

    client.get_object.reset_mock()

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        await object_storage.get_object('s3://my-bucket', 'HTTPS://host/k.txt', allow_absolute=True)

    assert client.get_object.call_args[0][0] == 'HTTPS://host/k.txt'


@pytest.mark.asyncio
async def test_put_object_data_types(tmp_path):
    work_path = f'file://{tmp_path}'

    # data is None (deprecated)
    with pytest.warns(DeprecationWarning, match='data=None.*1.0'):
        url1 = await object_storage.put_object(work_path, 'none.txt', data=None)

    assert url1 == f'{work_path}/none.txt'
    assert await object_storage.get_object(work_path, 'none.txt') == b''

    # data is bytearray (converted via bytes(data))
    url2 = await object_storage.put_object(work_path, 'ba.txt', data=bytearray(b'hello bytearray'))
    assert url2 == f'{work_path}/ba.txt'
    assert await object_storage.get_object(work_path, 'ba.txt') == b'hello bytearray'


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [{'length': 3}, {'part_size': 5}, {'length': 0}])
async def test_put_object_length_and_part_size_deprecated(tmp_path, kwargs):
    with pytest.warns(DeprecationWarning, match='length and part_size.*1.0') as record:
        await object_storage.put_object(f'file://{tmp_path}', 'x.txt', b'abc', **kwargs)

    assert record[0].filename == __file__
    assert await object_storage.get_object(f'file://{tmp_path}', 'x.txt') == b'abc'


@pytest.mark.asyncio
async def test_put_object_no_deprecation_for_plain_call(tmp_path):
    await object_storage.put_object(f'file://{tmp_path}', 'x.txt', io.BytesIO(b'abc'))
    await object_storage.put_object(f'file://{tmp_path}', 'y.txt', memoryview(b'abc'))


@pytest.mark.asyncio
async def test_put_object_stream(tmp_path):
    async def chunks():
        yield b'ab'
        yield bytearray(b'cd')

    url = await object_storage.put_object_stream(f'file://{tmp_path}', 'sub/s.bin', chunks(), size_hint=4)

    assert url == f'file://{tmp_path}/sub/s.bin'
    assert await object_storage.get_object(f'file://{tmp_path}', 'sub/s.bin') == b'abcd'


@pytest.mark.asyncio
async def test_put_object_stream_uses_get_client_and_validates_key(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.put_object_stream = AsyncMock()
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    async def chunks():
        yield b'x'

    it = chunks()
    await object_storage.put_object_stream('s3://bucket', 'a.bin', it)
    mock_client.put_object_stream.assert_awaited_once_with('s3://bucket/a.bin', it, size_hint=None)

    with pytest.raises(ValueError, match='empty'):
        await object_storage.put_object_stream('s3://bucket', '/', it)

    await it.aclose()


@pytest.mark.asyncio
async def test_list_objects_nested_same_name_addresses_the_right_object(mocker):
    # Listing the root of 's3://bucket/data' with prefix 'data': names are relative to the listed URL, so
    # 'data/x.txt' is s3://bucket/data/data/x.txt, never the top-level s3://bucket/data/x.txt
    _listing_client(
        mocker,
        ['x.txt', 'data/x.txt'],
        metadata={
            's3://bucket/data/x.txt': _meta('TOP-X'),
            's3://bucket/data/data/x.txt': _meta('NESTED-X'),
        },
    )

    with pytest.warns(DeprecationWarning, match='deduplication'):
        items = [obj async for obj in object_storage.list_objects('s3://bucket/data', 'data')]

    assert [(i['Key'], i['ETag']) for i in items] == [('data/x.txt', 'TOP-X'), ('data/data/x.txt', 'NESTED-X')]


@pytest.mark.asyncio
async def test_list_objects_with_prefix_uses_listed_url(mocker):
    client = _listing_client(mocker, ['a.txt'], metadata=lambda url: _meta())

    items = [obj async for obj in object_storage.list_objects('S3://bucket//root/', '/sub//')]

    client.list_objects_stream.assert_awaited_once_with('s3://bucket/root/sub')
    client.get_object_metadata.assert_awaited_once_with('s3://bucket/root/sub/a.txt')
    assert items[0]['Key'] == 'sub/a.txt'
    assert client.streams[0].closed


def test_object_storage_normalize_root_is_alias():
    assert object_storage.normalize_root is _paths.normalize_root


@pytest.mark.asyncio
async def test_put_object_error_handling(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.put_object = AsyncMock(side_effect=RuntimeError('Storage put failure'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.raises(RuntimeError, match='Storage put failure'):
        await object_storage.put_object('s3://bucket', 'file.txt', b'content')


@pytest.mark.asyncio
async def test_get_object_and_stream_error_handling(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.get_object = AsyncMock(side_effect=RuntimeError('Get object failure'))
    mock_client.get_object_stream = AsyncMock(side_effect=RuntimeError('Stream failure'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.raises(RuntimeError, match='Get object failure'):
        await object_storage.get_object('s3://bucket', 'file.txt')

    with pytest.raises(RuntimeError, match='Stream failure'):
        async with object_storage.get_object_stream('s3://bucket', 'file.txt'):
            pass


@pytest.mark.asyncio
async def test_list_objects_metadata_failure_keeps_legacy_values_and_warns(mocker, caplog):
    def metadata(url):
        if url.endswith('gone.txt'):
            raise FileNotFoundError(url)

        if url.endswith('broken.txt'):
            msg = 'Metadata unavailable'
            raise RuntimeError(msg)

        return _meta('E', 7)

    _listing_client(mocker, ['ok.txt', 'gone.txt'], ['broken.txt'], metadata=metadata)

    with (
        caplog.at_level(logging.WARNING, logger='python3_commons.object_storage'),
        pytest.warns(DeprecationWarning, match='in 1.0 missing objects are skipped') as record,
    ):
        items = {obj['Key']: obj async for obj in object_storage.list_objects('s3://bucket')}

    assert len(record) == 1  # one warning per call, not per item
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert items['ok.txt']['Size'] == 7
    assert items['ok.txt']['LastModified'].tzinfo is not None

    for key in ('gone.txt', 'broken.txt'):
        assert items[key]['Size'] == 0
        assert items[key]['ETag'] is None
        assert items[key]['LastModified'].tzinfo is None


@pytest.mark.asyncio
async def test_list_objects_missing_timestamp_is_legacy(mocker):
    _listing_client(mocker, ['a.txt'], metadata=lambda url: {'size_bytes': 3, 'e_tag': 'E'})

    with pytest.warns(DeprecationWarning, match='in 1.0'):
        items = [obj async for obj in object_storage.list_objects('s3://bucket')]

    assert items[0]['Size'] == 3
    assert isinstance(items[0]['LastModified'], datetime)


@pytest.mark.asyncio
async def test_list_objects_successful_listing_does_not_warn(mocker, caplog):
    _listing_client(mocker, ['a.txt', 'b/c.txt'], metadata=lambda url: _meta())

    with caplog.at_level(logging.WARNING, logger='python3_commons.object_storage'):
        items = [obj async for obj in object_storage.list_objects('s3://bucket', recursive=False)]

    assert [i['Key'] for i in items] == ['a.txt']
    assert not caplog.records


@pytest.mark.asyncio
async def test_list_objects_without_metadata(mocker):
    client = _listing_client(mocker, ['a.txt', 'b/c.txt'], metadata=lambda url: _meta())

    items = [obj async for obj in object_storage.list_objects('s3://bucket', 'p', with_metadata=False)]

    assert items == [{'Key': 'p/a.txt'}, {'Key': 'p/b/c.txt'}]
    client.get_object_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_list_objects_metadata_concurrency_is_bounded(mocker):
    state = {'now': 0, 'max': 0}

    async def metadata(url):
        state['now'] += 1
        state['max'] = max(state['max'], state['now'])
        await asyncio.sleep(0.001)
        state['now'] -= 1

        return _meta()

    _listing_client(mocker, [f'{i}.txt' for i in range(40)], metadata=metadata)

    items = [obj async for obj in object_storage.list_objects('s3://bucket')]

    assert len(items) == 40
    assert 1 < state['max'] <= 16


@pytest.mark.asyncio
async def test_list_objects_batch_failure_surfaces_the_real_error(mocker):
    class Boom(BaseException):
        pass

    def metadata(url):
        if url.endswith('b.txt'):
            raise Boom

        return _meta()

    client = _listing_client(mocker, ['a.txt'], ['b.txt'], metadata=metadata)
    listing = object_storage.list_objects('s3://bucket')

    first = await anext(listing)  # the consumer now holds an item while the next batch fails
    assert first['Key'] == 'a.txt'

    with pytest.raises(BaseExceptionGroup) as excinfo:
        await anext(listing)

    assert excinfo.group_contains(Boom)
    assert not excinfo.group_contains(asyncio.CancelledError)
    assert client.streams[0].closed


@pytest.mark.asyncio
async def test_list_objects_early_break_closes_the_name_stream(mocker):
    client = _listing_client(mocker, ['a.txt', 'b.txt'], ['c.txt'], metadata=lambda url: _meta())

    async with contextlib.aclosing(object_storage.list_objects('s3://bucket')) as listing:
        async for _ in listing:
            break

    assert client.streams[0].closed


def _open_fds():
    return len(list(Path('/proc/self/fd').iterdir()))


@pytest.mark.asyncio
async def test_get_object_streams_early_break_restores_fd_count(tmp_path):
    work_path = f'file://{tmp_path}'

    for i in range(5):
        await object_storage.put_object(work_path, f'f{i}.txt', b'x' * 10)

    before = _open_fds()

    async with contextlib.aclosing(object_storage.get_object_streams(work_path)) as streams:
        async for _, _, stream in streams:
            assert await stream.read() == b'x' * 10

            break

    assert _open_fds() == before


@pytest.mark.asyncio
async def test_get_objects_holds_no_stream_open_while_the_consumer_runs(tmp_path, mocker):
    work_path = f'file://{tmp_path}'

    for i in range(3):
        await object_storage.put_object(work_path, f'f{i}.txt', f'data{i}'.encode())

    open_streams = []
    real = object_storage.get_object_stream

    @contextlib.asynccontextmanager
    async def tracking(*args, **kwargs):
        async with real(*args, **kwargs) as stream:
            open_streams.append(stream)

            try:
                yield stream
            finally:
                open_streams.remove(stream)

    mocker.patch('python3_commons.object_storage.get_object_stream', tracking)
    seen = {}

    async with contextlib.aclosing(object_storage.get_objects(work_path)) as objects:
        async for name, modified, data in objects:
            assert not open_streams
            assert isinstance(modified, datetime)
            seen[name] = data

    assert seen == {f'f{i}.txt': f'data{i}'.encode() for i in range(3)}


@pytest.mark.asyncio
async def test_remove_object_error_handling(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_object = AsyncMock(side_effect=RuntimeError('Delete failure'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.raises(RuntimeError, match='Delete failure'):
        await object_storage.remove_object('s3://bucket', 'file.txt')


@pytest.mark.asyncio
async def test_remove_objects_prefix_error(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_objects_by_prefix = AsyncMock(side_effect=RuntimeError('Prefix delete failure'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.raises(RuntimeError, match='Prefix delete failure'):
        await object_storage.remove_objects('s3://bucket', prefix='folder/')


@pytest.mark.asyncio
async def test_remove_objects_names_partial_error(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_object = AsyncMock(side_effect=[None, RuntimeError('Delete f2 failure'), None])
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    errors = await object_storage.remove_objects('s3://bucket', object_names=['f1', 'f2', 'f3'])
    assert errors is not None
    assert [(e['Key'], e['Error']) for e in errors] == [('f2', 'Delete f2 failure')]
    assert isinstance(errors[0]['Exception'], RuntimeError)
    assert 'object storage url: s3://bucket/f2' in errors[0]['Exception'].__notes__

    # prefix takes precedence when both prefix and object_names are supplied
    mock_client.delete_objects_by_prefix = AsyncMock()
    await object_storage.remove_objects('s3://bucket', prefix='folder/', object_names=['f1'])
    mock_client.delete_objects_by_prefix.assert_called_once()


def test_object_storage_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, 'object_storage_client', None)
    monkeypatch.delitem(sys.modules, 'python3_commons.object_storage')

    with pytest.raises(RuntimeError, match=r'Install python3-commons\[object-storage\] to use this feature'):
        importlib.import_module('python3_commons.object_storage')


@pytest.mark.parametrize(
    'key',
    [
        '../x',
        'a/../../x',
        'a/./b',
        '.',
        '..',
        '/file:///etc/hostname',
        's3://other/x',
        'a\\..\\x',
        '%2e%2e/x',
        '%2E%2E',
        'a%2F..%2F..%2Fx',
        '..%2Fx',
        'a%00b',
        'a\x00b',
    ],
)
def test_clean_key_rejects(key):
    with pytest.raises(ValueError, match='object key'):
        _paths.clean_key(key)


@pytest.mark.parametrize(
    ('key', 'expected'),
    [
        ('', ''),
        ('/', ''),
        ('//', ''),
        ('a//b/', 'a/b'),
        ('/a/b', 'a/b'),
        ('a..b/.hidden/..x', 'a..b/.hidden/..x'),
        ('a%20b', 'a%20b'),
        ('a\\b', 'a\\b'),
    ],
)
def test_clean_key_accepts(key, expected):
    assert _paths.clean_key(key) == expected


@pytest.fixture
def storage_tree(tmp_path):
    work = tmp_path / 'wp'
    work.mkdir()
    (work / 'tenant-a').mkdir()
    (work / 'tenant-a' / '1.txt').write_bytes(b'one')
    (work / 'top.txt').write_bytes(b'top')
    (tmp_path / 'secret.txt').write_bytes(b'outside')

    return tmp_path, f'file://{work}'


ESCAPES = ['../secret.txt', 'a/../../secret.txt', '..\\secret.txt', '%2e%2e/secret.txt', '..%2Fsecret.txt']


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ESCAPES)
async def test_file_root_escape_rejected(storage_tree, key):
    tmp_path, work_path = storage_tree

    with pytest.raises(ValueError, match='object key'):
        await object_storage.get_object(work_path, key)

    with pytest.raises(ValueError, match='object key'):
        async with object_storage.get_object_stream(work_path, key):
            pass

    with pytest.raises(ValueError, match='object key'):
        await object_storage.put_object(work_path, key, b'pwned')

    with pytest.raises(ValueError, match='object key'):
        await object_storage.remove_object(work_path, key)

    assert (tmp_path / 'secret.txt').read_bytes() == b'outside'
    assert not (tmp_path / 'written.txt').exists()


@pytest.mark.asyncio
async def test_file_root_leading_slash_scheme_override_rejected(storage_tree):
    tmp_path, work_path = storage_tree
    target = f'/file://{tmp_path}/secret.txt'

    with pytest.raises(ValueError, match='object key'):
        await object_storage.get_object(work_path, target)

    with pytest.raises(ValueError, match='object key'):
        object_storage.build_url('s3://bucket/audit', '/s3://other-bucket/secret.txt')


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['/', '//', ''])
async def test_remove_objects_empty_prefix_refused(storage_tree, prefix):
    tmp_path, work_path = storage_tree

    with pytest.raises(ValueError, match='remove_all'):
        await object_storage.remove_objects(work_path, prefix=prefix)

    assert (tmp_path / 'wp' / 'top.txt').exists()
    assert (tmp_path / 'wp' / 'tenant-a' / '1.txt').exists()


@pytest.mark.asyncio
async def test_remove_objects_empty_tenant_prefix_refused(storage_tree):
    tmp_path, work_path = storage_tree
    tenant = ''

    with pytest.raises(ValueError, match='remove_all'):
        await object_storage.remove_objects(work_path, prefix=f'{tenant}/')

    assert (tmp_path / 'wp' / 'top.txt').exists()


@pytest.mark.asyncio
async def test_remove_objects_remove_all_explicit(storage_tree):
    tmp_path, work_path = storage_tree

    await object_storage.remove_objects(work_path, prefix='', remove_all=True)

    assert not (tmp_path / 'wp' / 'top.txt').exists()
    assert not (tmp_path / 'wp' / 'tenant-a' / '1.txt').exists()


@pytest.mark.asyncio
async def test_remove_objects_none_prefix_still_noop(storage_tree):
    tmp_path, work_path = storage_tree

    assert await object_storage.remove_objects(work_path, prefix=None) is None
    assert (tmp_path / 'wp' / 'top.txt').exists()


@pytest.mark.asyncio
async def test_remove_objects_valid_prefix_on_file_root(storage_tree):
    tmp_path, work_path = storage_tree

    await object_storage.remove_objects(work_path, prefix='tenant-a/')

    assert not (tmp_path / 'wp' / 'tenant-a' / '1.txt').exists()
    assert (tmp_path / 'wp' / 'top.txt').exists()


@pytest.mark.asyncio
async def test_remove_objects_names_normalising_to_empty_are_errors(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_object = AsyncMock()
    mock_client.delete_objects_by_prefix = AsyncMock()
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    errors = await object_storage.remove_objects('s3://bucket', object_names=['', '/', '../x', 'ok.txt'])

    assert errors is not None
    assert [e['Key'] for e in errors] == ['', '/', '../x']
    mock_client.delete_object.assert_awaited_once_with('s3://bucket/ok.txt')
    mock_client.delete_objects_by_prefix.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['/', '//', '', '../x'])
async def test_empty_or_unsafe_prefix_never_reaches_client(mocker, prefix):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_objects_by_prefix = AsyncMock()
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.raises(ValueError, match=r'remove_all|object key'):
        await object_storage.remove_objects('s3://bucket', prefix=prefix)

    mock_client.delete_objects_by_prefix.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['', '/', '//'])
async def test_empty_key_refused(mocker, key):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.put_object = AsyncMock()
    mock_client.get_object = AsyncMock()
    mock_client.get_object_stream = AsyncMock()
    mock_client.delete_object = AsyncMock()
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.raises(ValueError, match='empty'):
        await object_storage.put_object('s3://bucket', key, b'x')

    with pytest.raises(ValueError, match='empty'):
        await object_storage.get_object('s3://bucket', key)

    with pytest.raises(ValueError, match='empty'):
        async with object_storage.get_object_stream('s3://bucket', key):
            pass

    with pytest.raises(ValueError, match='empty'):
        await object_storage.remove_object('s3://bucket', key)

    mock_client.put_object.assert_not_called()
    mock_client.get_object.assert_not_called()
    mock_client.get_object_stream.assert_not_called()
    mock_client.delete_object.assert_not_called()


def _fd_count() -> int:
    return sum(1 for _ in Path('/proc/self/fd').iterdir())


@pytest.mark.skipif(not Path('/proc/self/fd').is_dir(), reason='needs /proc')
@pytest.mark.asyncio
async def test_get_object_stream_closes_stream(tmp_path):
    work_path = f'file://{tmp_path}'
    await object_storage.put_object(work_path, 'big.bin', b'x' * (8 * 1024 * 1024))
    before = _fd_count()

    async with object_storage.get_object_stream(work_path, 'big.bin') as stream:
        assert await stream.next()  # partial read

    assert _fd_count() == before


@pytest.mark.asyncio
async def test_get_object_stream_closes_on_caller_error_and_does_not_log_it(mocker, caplog):
    async def body():
        async with object_storage.get_object_stream('s3://bucket', 'file.txt'):
            msg = 'bug in caller code'
            raise ValueError(msg)

    raw = mocker.MagicMock()
    raw.aclose = AsyncMock()
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.get_object_stream = AsyncMock(return_value=raw)
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError, match='bug in caller code'):
        await body()

    raw.aclose.assert_awaited_once()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_get_object_stream_storage_failure_is_noted_not_logged(mocker, caplog):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.get_object_stream = AsyncMock(side_effect=RuntimeError('Stream failure'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError, match='Stream failure') as excinfo:
        async with object_storage.get_object_stream('s3://bucket', 'file.txt'):
            pass

    assert excinfo.value.__notes__ == ['object storage url: s3://bucket/file.txt']
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_missing_object_raises_with_a_note_and_logs_no_error(tmp_path, caplog):
    work_path = f'file://{tmp_path}'

    with caplog.at_level(logging.DEBUG):
        for call in (
            lambda: object_storage.get_object(work_path, 'nope.txt'),
            lambda: object_storage.remove_object(work_path, 'nope.txt'),
        ):
            with pytest.raises(FileNotFoundError) as excinfo:
                await call()

            assert excinfo.value.__notes__ == [f'object storage url: {work_path}/nope.txt']

        with pytest.raises(FileNotFoundError) as excinfo:
            async with object_storage.get_object_stream(work_path, 'nope.txt') as stream:
                await stream.read()

        assert f'object storage url: {work_path}/nope.txt' in excinfo.value.__notes__

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_put_object_and_stream_failures_are_noted_not_logged(mocker, caplog):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.put_object = AsyncMock(side_effect=RuntimeError('put failure'))
    mock_client.put_object_stream = AsyncMock(side_effect=RuntimeError('stream failure'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    async def chunks():
        yield b'x'

    it = chunks()

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match='put failure') as put_info:
            await object_storage.put_object('s3://bucket', 'a.txt', b'x')

        with pytest.raises(RuntimeError, match='stream failure') as stream_info:
            await object_storage.put_object_stream('s3://bucket', 'a.bin', it)

    await it.aclose()
    assert put_info.value.__notes__ == ['object storage url: s3://bucket/a.txt']
    assert stream_info.value.__notes__ == ['object storage url: s3://bucket/a.bin']
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_remove_objects_deletes_concurrently_and_bounded(mocker):
    state = {'now': 0, 'max': 0}

    async def delete(url):
        state['now'] += 1
        state['max'] = max(state['max'], state['now'])
        await asyncio.sleep(0.001)
        state['now'] -= 1

    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_object = AsyncMock(side_effect=delete)
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    assert await object_storage.remove_objects('s3://bucket', object_names=[f'f{i}' for i in range(40)]) is None
    assert mock_client.delete_object.await_count == 40
    assert 1 < state['max'] <= 16


@pytest.mark.asyncio
async def test_remove_objects_raise_errors_gives_an_exception_group(mocker, caplog):
    async def delete(url):
        if url.endswith(('f2', 'f3')):
            msg = f'failed {url}'
            raise RuntimeError(msg)

    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_object = AsyncMock(side_effect=delete)
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with caplog.at_level(logging.DEBUG), pytest.raises(ExceptionGroup) as excinfo:
        await object_storage.remove_objects('s3://bucket', object_names=['f1', 'f2', 'f3', '../x'], raise_errors=True)

    assert [type(e) for e in excinfo.value.exceptions] == [RuntimeError, RuntimeError, ValueError]
    assert excinfo.value.exceptions[0].__notes__ == ['object storage url: s3://bucket/f2']
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    # without raise_errors the same failures are returned, in name order
    errors = await object_storage.remove_objects('s3://bucket', object_names=['f1', 'f2', 'f3'])
    assert errors is not None
    assert [e['Key'] for e in errors] == ['f2', 'f3']


@pytest.mark.asyncio
async def test_validation_errors_are_not_logged_as_storage_failures(mocker, caplog):
    mocker.patch('python3_commons.object_storage.get_client', return_value=mocker.MagicMock(spec=ObjectStorageClient))

    with caplog.at_level(logging.DEBUG), pytest.raises(ValueError, match='object key'):
        await object_storage.get_object('s3://bucket', '../x')

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_dedup_warning_names_both_urls_and_keeps_legacy_url():
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        url = object_storage.build_url('s3://bucket/data', 'data/x.txt')

    assert url == 's3://bucket/data/x.txt'
    message = str(record[0].message)
    assert 's3://bucket/data/x.txt' in message
    assert 's3://bucket/data/data/x.txt' in message
    assert record[0].filename == __file__


def test_no_warning_when_plain_join_equals_legacy():
    with warnings.catch_warnings():
        warnings.simplefilter('error')

        assert object_storage.build_url('s3://bucket/data', 'x/data/y.txt') == 's3://bucket/data/x/data/y.txt'
        assert object_storage.build_url('s3://bucket/data', '/x//y/') == 's3://bucket/data/x/y'


def test_absolute_url_deprecated_unless_allowed():
    with pytest.warns(DeprecationWarning, match='1.0') as record:
        url = object_storage.build_url('s3://bucket', 'gs://other/x.txt')

    assert url == 'gs://other/x.txt'
    assert record[0].filename == __file__

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        assert object_storage.build_url('s3://bucket', 'gs://other/x.txt', allow_absolute=True) == 'gs://other/x.txt'


@pytest.mark.asyncio
async def test_absolute_url_operations_warn_and_work(tmp_path):
    target = f'file://{tmp_path}/abs.txt'
    work_path = f'file://{tmp_path}/wp'

    with pytest.warns(DeprecationWarning, match='1.0'):
        await object_storage.put_object(work_path, target, b'abs')

    assert (tmp_path / 'abs.txt').read_bytes() == b'abs'

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        assert await object_storage.get_object(work_path, target, allow_absolute=True) == b'abs'


@pytest.mark.asyncio
async def test_remove_objects_prefix_deduplicated_to_root_refused(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.delete_objects_by_prefix = AsyncMock()
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    with pytest.warns(DeprecationWarning, match='1.0'), pytest.raises(ValueError, match='remove_all'):
        await object_storage.remove_objects('s3://bucket/data', prefix='data/')

    mock_client.delete_objects_by_prefix.assert_not_called()


# ---- ObjectStorage (2.F4) ----


@pytest.fixture
def storage(tmp_path):
    return object_storage.ObjectStorage(f'file://{tmp_path}//data/', client=ObjectStorageClient())


@pytest.mark.asyncio
async def test_object_storage_round_trip_on_a_file_root(storage, tmp_path):
    assert storage.root == f'file://{tmp_path}/data'
    assert storage.url('a/b.txt') == f'file://{tmp_path}/data/a/b.txt'

    assert await storage.put_object('a/one.txt', b'one') == f'file://{tmp_path}/data/a/one.txt'
    await storage.put_object('two.txt', io.BytesIO(b'two'))

    async def chunks():
        yield b'th'
        yield b'ree'

    await storage.put_object_stream('three.txt', chunks(), size_hint=5)

    assert await storage.get_object('a/one.txt') == b'one'

    listing = {obj['Key']: obj async for obj in storage.list_objects()}
    assert set(listing) == {'a/one.txt', 'two.txt', 'three.txt'}
    assert listing['two.txt']['Size'] == 3
    assert {o['Key'] async for o in storage.list_objects('a')} == {'a/one.txt'}
    assert {o['Key'] async for o in storage.list_objects(recursive=False)} == {'two.txt', 'three.txt'}

    async with contextlib.aclosing(storage.get_object_streams('a')) as streams:
        assert [(key, await stream.read()) async for key, _, stream in streams] == [('a/one.txt', b'one')]

    async with contextlib.aclosing(storage.get_objects()) as objects:
        assert {key: data async for key, _, data in objects} == {
            'a/one.txt': b'one',
            'two.txt': b'two',
            'three.txt': b'three',
        }

    await storage.remove_object('two.txt')
    assert await storage.remove_objects(object_names=['three.txt', 'missing.txt']) is not None
    await storage.remove_objects('a/')
    assert [o async for o in storage.list_objects()] == []


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['../x', 'a/../x', '%2e%2e/x', 'file:///etc/passwd', 's3://other/x', 'a\x00b', ''])
async def test_object_storage_rejects_unsafe_and_empty_keys(storage, key):
    for call in (
        lambda: storage.put_object(key, b'x'),
        lambda: storage.get_object(key),
        lambda: storage.remove_object(key),
    ):
        with pytest.raises(ValueError, match='object key'):
            await call()


@pytest.mark.asyncio
async def test_object_storage_never_leaves_its_root_or_dedups(storage, tmp_path):
    await storage.put_object('data/x.txt', b'x')  # no prefix deduplication: <root>/data/x.txt

    assert (tmp_path / 'data' / 'data' / 'x.txt').read_bytes() == b'x'


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['', '/', '//'])
async def test_object_storage_refuses_an_empty_prefix_unless_remove_all(storage, prefix):
    await storage.put_object('keep.txt', b'x')

    with pytest.raises(ValueError, match='remove_all'):
        await storage.remove_objects(prefix)

    assert await storage.get_object('keep.txt') == b'x'

    await storage.remove_objects(prefix, remove_all=True)
    assert [o async for o in storage.list_objects()] == []
    assert await storage.remove_objects() is None


@pytest.mark.asyncio
async def test_object_storage_errors_carry_a_note_and_are_not_logged(storage, tmp_path, caplog):
    with caplog.at_level(logging.DEBUG), pytest.raises(FileNotFoundError) as excinfo:
        await storage.get_object('nope.txt')

    assert excinfo.value.__notes__ == [f'object storage url: file://{tmp_path}/data/nope.txt']
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    errors = await storage.remove_objects(object_names=['nope.txt', '../x'])
    assert [e['Key'] for e in errors] == ['nope.txt', '../x']

    with pytest.raises(ExceptionGroup):
        await storage.remove_objects(object_names=['nope.txt'], raise_errors=True)


def test_object_storage_blank_root_is_an_error():
    with pytest.raises(ValueError, match='empty'):
        object_storage.ObjectStorage('  ', client=ObjectStorageClient())
