import os

import pytest
from object_storage_client import ObjectStorageClient

from python3_commons.conf import object_storage_settings


@pytest.mark.functional
@pytest.mark.asyncio
async def test_s3_object_lifecycle():
    work_path = object_storage_settings.work_path or os.environ.get('OBJECT_STORAGE_WORK_PATH')
    assert work_path is not None, 'OBJECT_STORAGE_WORK_PATH environment variable must be set to run this test'

    # Ensure work_path doesn't end with a slash for consistent joining
    work_path = work_path.rstrip('/')
    base_url = work_path if work_path.startswith(('s3://', 'file://', 'gs://', 'az://')) else f's3://{work_path}/'
    if not base_url.endswith('/'):
        base_url = f'{base_url}/'

    client = ObjectStorageClient()

    # 1. Create a file and put it to storage
    file_url = f'{base_url}test_file_s3.txt'
    content = b'Hello, S3 Object Storage!'
    await client.put_object(file_url, content)

    # 2. List it in bucket
    listing = await client.list_objects(base_url)
    assert 'test_file_s3.txt' in listing, 'File should be in the list'

    # 3. Move it
    moved_file_url = f'{base_url}moved_test_file_s3.txt'
    await client.move_object(file_url, moved_file_url)

    # 4. List again to check the movement
    list_after_move = await client.list_objects(base_url)

    assert 'test_file_s3.txt' not in list_after_move, 'Old file should NOT be in the list'

    assert 'moved_test_file_s3.txt' in list_after_move, 'Moved file should be in the list'

    # 5. Delete
    await client.delete_object(moved_file_url)

    # 6. List again to check the deletion
    list_after_delete = await client.list_objects(base_url)

    assert 'moved_test_file_s3.txt' not in list_after_delete, 'Deleted file should NOT be in the list'
