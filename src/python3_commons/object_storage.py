import logging
from io import IOBase
from typing import Generator

from minio import Minio
from minio.datatypes import Object

from python3_commons import minio
from python3_commons.conf import s3_settings

logger = logging.getLogger(__name__)
__CLIENT = None


def get_s3_client() -> Minio:
    global __CLIENT

    if not __CLIENT and s3_settings.s3_endpoint_url:
        __CLIENT = minio.get_client(
            s3_settings.s3_endpoint_url,
            s3_settings.s3_region_name,
            s3_settings.s3_access_key_id,
            s3_settings.s3_secret_access_key,
            s3_settings.s3_secure
        )

    return __CLIENT


def get_absolute_path(path: str) -> str:
    if path.startswith('/'):
        path = path[1:]

    if bucket_root := s3_settings.s3_bucket_root:
        path = f'{bucket_root[:1] if bucket_root.startswith("/") else bucket_root}/{path}'

    return path


def put_object(bucket_name: str, path: str, data: IOBase, length: int):
    s3_client = get_s3_client()

    if s3_client:
        path = get_absolute_path(path)
        result = s3_client.put_object(bucket_name, path, data, length)

        logger.debug(f'Stored object into object storage: {bucket_name}:{path}')

        return result.location
    else:
        logger.warning(f'No S3 client available, skipping object put')


def get_object_stream(bucket_name: str, path: str):
    s3_client = get_s3_client()

    if s3_client:
        path = get_absolute_path(path)
        logger.debug(f'Getting object from object storage: {bucket_name}:{path}')

        try:
            response = s3_client.get_object(bucket_name, path)
        except Exception as e:
            logger.debug(f'Failed getting object from object storage: {bucket_name}:{path}', exc_info=e)

            raise

        return response
    else:
        logger.warning(f'No S3 client available, skipping object put')


def get_object(bucket_name: str, path: str) -> bytes:
    response = get_object_stream(bucket_name, path)

    try:
        body = response.read()
    finally:
        response.close()
        response.release_conn()

    logger.debug(f'Loaded object from object storage: {bucket_name}:{path}')

    return body


def list_objects(bucket_name: str, path: str, recursive: bool = True) -> Generator[Object, None, None]:
    path = get_absolute_path(path)
    s3_client = get_s3_client()

    yield from s3_client.list_objects(bucket_name, path, recursive)


def get_objects(bucket_name: str, path: str, recursive: bool = True) -> Generator[tuple[str, bytes], None, None]:
    for obj in list_objects(bucket_name, path, recursive):
        object_name = obj.object_name

        if obj.size:
            data = get_object(bucket_name, object_name)
        else:
            data = b''

        yield object_name, data
