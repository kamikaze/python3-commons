import io
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Iterable

from minio import Minio
from minio.datatypes import Object
from minio.deleteobjects import DeleteObject, DeleteError

from python3_commons.conf import s3_settings, S3Settings
from python3_commons.helpers import SingletonMeta

logger = logging.getLogger(__name__)


class ObjectStorage(metaclass=SingletonMeta):
    def __init__(self, settings: S3Settings):
        if not s3_settings.s3_endpoint_url:
            raise ValueError('s3_settings.s3_endpoint_url must be set')

        self._client = Minio(
            settings.s3_endpoint_url,
            region=settings.s3_region_name,
            access_key=settings.s3_access_key_id.get_secret_value(),
            secret_key=settings.s3_secret_access_key.get_secret_value(),
            secure=settings.s3_secure,
            cert_check=settings.s3_cert_verify
        )

    def get_client(self) -> Minio:
        return self._client


def get_absolute_path(path: str) -> str:
    if path.startswith('/'):
        path = path[1:]

    if bucket_root := s3_settings.s3_bucket_root:
        path = f'{bucket_root[:1] if bucket_root.startswith('/') else bucket_root}/{path}'

    return path


def put_object(bucket_name: str, path: str, data: io.BytesIO, length: int, part_size: int = 0) -> str:
    if s3_client := ObjectStorage(s3_settings).get_client():
        result = s3_client.put_object(bucket_name, path, data, length, part_size=part_size)

        logger.debug(f'Stored object into object storage: {bucket_name}:{path}')

        return result.location
    else:
        logger.warning(f'No S3 client available, skipping object put')


@contextmanager
def get_object_stream(bucket_name: str, path: str):
    if s3_client := ObjectStorage(s3_settings).get_client():
        logger.debug(f'Getting object from object storage: {bucket_name}:{path}')

        try:
            response = s3_client.get_object(bucket_name, path)
        except Exception as e:
            logger.debug(f'Failed getting object from object storage: {bucket_name}:{path}', exc_info=e)

            raise

        yield response

        response.close()
        response.release_conn()
    else:
        logger.warning(f'No S3 client available, skipping object put')


def get_object(bucket_name: str, path: str) -> bytes:
    with get_object_stream(bucket_name, path) as stream:
        body = stream.read()

    logger.debug(f'Loaded object from object storage: {bucket_name}:{path}')

    return body


def list_objects(bucket_name: str, prefix: str, recursive: bool = True) -> Generator[Object, None, None]:
    s3_client = ObjectStorage(s3_settings).get_client()

    yield from s3_client.list_objects(bucket_name, prefix=prefix, recursive=recursive)


def get_objects(bucket_name: str, path: str,
                recursive: bool = True) -> Generator[tuple[str, datetime, bytes], None, None]:
    for obj in list_objects(bucket_name, path, recursive):
        object_name = obj.object_name

        if obj.size:
            data = get_object(bucket_name, object_name)
        else:
            data = b''

        yield object_name, obj.last_modified, data


def remove_object(bucket_name: str, object_name: str):
    s3_client = ObjectStorage(s3_settings).get_client()
    s3_client.remove_object(bucket_name, object_name)


def remove_objects(bucket_name: str, prefix: str = None,
                   object_names: Iterable[str] = None) -> Iterable[DeleteError] | None:
    s3_client = ObjectStorage(s3_settings).get_client()

    if prefix:
        delete_object_list = map(
            lambda obj: DeleteObject(obj.object_name), s3_client.list_objects(bucket_name, prefix=prefix,
                                                                              recursive=True)
        )
    elif object_names:
        delete_object_list = map(DeleteObject, object_names)
    else:
        return None

    errors = s3_client.remove_objects(bucket_name, delete_object_list)

    return errors
