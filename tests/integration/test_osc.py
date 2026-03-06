import os

import pytest
from object_storage_client import ObjectStorageClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_s3_object_lifecycle():
    bucket = os.environ.get('S3_BUCKET')
    assert bucket is not None, 'S3_BUCKET environment variable must be set to run this test'

    # Ensure bucket doesn't end with a slash for consistent joining
    bucket = bucket.rstrip('/')
    bucket_url = f's3://{bucket}/'

    client = ObjectStorageClient()

    # 1. Create a file and put it to storage
    file_url = f'{bucket_url}/test_file_s3.txt'
    content = b'Hello, S3 Object Storage!'
    await client.put(file_url, content)

    # 2. List it in bucket
    listing = await client.list(bucket_url)
    assert 'test_file_s3.txt' in listing, 'File should be in the list'

    # 3. Move it
    moved_file_url = f'{bucket_url}/moved_test_file_s3.txt'
    await client.move_object(file_url, moved_file_url)

    # 4. List again to check the movement
    list_after_move = await client.list(bucket_url)

    assert 'test_file_s3.txt' not in list_after_move, 'Old file should NOT be in the list'

    assert 'moved_test_file_s3.txt' in list_after_move, 'Moved file should be in the list'

    # 5. Delete
    await client.delete(moved_file_url)

    # 6. List again to check the deletion
    list_after_delete = await client.list(bucket_url)

    assert 'moved_test_file_s3.txt' not in list_after_delete, 'Deleted file should NOT be in the list'
