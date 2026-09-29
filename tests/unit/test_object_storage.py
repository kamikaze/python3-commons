import importlib
import io
import sys
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from object_storage_client import ObjectStorageClient

from python3_commons import object_storage
from python3_commons.object_storage import ObjectStorageStream


def test_build_url():
    # Plain bucket name defaults to s3://
    assert object_storage.build_url('my-bucket', 'file.txt') == 's3://my-bucket/file.txt'
    # S3 schemes
    assert object_storage.build_url('s3://my-bucket', 'file.txt') == 's3://my-bucket/file.txt'
    assert object_storage.build_url('s3://my-bucket/prefix', 'file.txt') == 's3://my-bucket/prefix/file.txt'
    # Deduplication of prefix
    assert object_storage.build_url('s3://my-bucket/prefix', 'prefix/file.txt') == 's3://my-bucket/prefix/file.txt'
    assert (
        object_storage.build_url('s3://my-bucket/prefix', 's3://my-bucket/prefix/file.txt')
        == 's3://my-bucket/prefix/file.txt'
    )
    # Deduplication with bucket in path
    assert object_storage.build_url('my-bucket', 'my-bucket/file.txt') == 's3://my-bucket/file.txt'
    # Local paths
    assert object_storage.build_url('file:///var/data', 'file.txt') == 'file:///var/data/file.txt'
    assert object_storage.build_url('/var/data', 'file.txt') == '/var/data/file.txt'
    assert object_storage.build_url('/var/data', 'var/data/file.txt') == '/var/data/file.txt'
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
    url = await object_storage.put_object(work_path, 'test1.txt', io.BytesIO(content), len(content))
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
    stream = ObjectStorageStream(MockStream([b'chunk1', b'chunk2', None]))
    assert await stream.read() == b'chunk1chunk2'

    # Test read() on empty stream
    empty_stream = ObjectStorageStream(MockStream([None]))
    assert await empty_stream.read() == b''

    # Test StopAsyncIteration on empty/None
    stream2 = ObjectStorageStream(MockStream([None]))
    with pytest.raises(StopAsyncIteration):
        await stream2.__anext__()


def test_build_url_additional_schemes_and_deduplication():
    assert object_storage.build_url('gs://my-bucket', 'file.txt') == 'gs://my-bucket/file.txt'
    assert object_storage.build_url('az://my-bucket', 'file.txt') == 'az://my-bucket/file.txt'
    assert object_storage.build_url('s3://my-bucket/sub', 'sub/file.txt') == 's3://my-bucket/sub/file.txt'
    assert object_storage.build_url('s3://my-bucket', 'gs://other-bucket/file.txt') == 'gs://other-bucket/file.txt'
    assert object_storage.build_url('s3://my-bucket', 'az://other-bucket/file.txt') == 'az://other-bucket/file.txt'
    assert object_storage.build_url('my-bucket') == 's3://my-bucket'


@pytest.mark.asyncio
async def test_put_object_data_types(tmp_path):
    work_path = f'file://{tmp_path}'

    # data is None
    url1 = await object_storage.put_object(work_path, 'none.txt', data=None)
    assert url1 == f'{work_path}/none.txt'
    assert await object_storage.get_object(work_path, 'none.txt') == b''

    # data is bytearray (converted via bytes(data))
    url2 = await object_storage.put_object(work_path, 'ba.txt', data=bytearray(b'hello bytearray'))
    assert url2 == f'{work_path}/ba.txt'
    assert await object_storage.get_object(work_path, 'ba.txt') == b'hello bytearray'


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
async def test_list_objects_metadata_fallback(mocker):
    mock_client = mocker.MagicMock(spec=ObjectStorageClient)
    mock_client.list_objects = AsyncMock(return_value=['obj1.txt'])
    mock_client.get_object_metadata = AsyncMock(side_effect=RuntimeError('Metadata unavailable'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=mock_client)

    items = [obj async for obj in object_storage.list_objects('s3://bucket')]
    assert len(items) == 1
    assert items[0]['Key'] == 'obj1.txt'
    assert items[0]['Size'] == 0
    assert items[0]['ETag'] is None
    assert isinstance(items[0]['LastModified'], datetime)


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
    assert errors == [{'Key': 'f2', 'Error': 'Delete f2 failure'}]

    # prefix takes precedence when both prefix and object_names are supplied
    mock_client.delete_objects_by_prefix = AsyncMock()
    await object_storage.remove_objects('s3://bucket', prefix='folder/', object_names=['f1'])
    mock_client.delete_objects_by_prefix.assert_called_once()


def test_object_storage_import_error():
    with (
        patch.dict(sys.modules, {'object_storage_client': None}),
        pytest.raises(RuntimeError, match=r'Install python3-commons\[object-storage\] to use this feature'),
    ):
        importlib.reload(sys.modules['python3_commons.object_storage'])
    importlib.reload(sys.modules['python3_commons.object_storage'])
