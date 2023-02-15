import logging
from io import RawIOBase

from minio import Minio
from pydantic import SecretStr

from python3_commons import minio

logger = logging.getLogger(__name__)
_CLIENT = None


def get_s3_client(endpoint_url: str, region_name: str, access_key_id: SecretStr, secret_access_key: SecretStr,
                  secure: bool = True) -> Minio:
    global _CLIENT

    if not _CLIENT:
        _CLIENT = minio.get_client(endpoint_url, region_name, access_key_id, secret_access_key, secure)

    return _CLIENT


def get_absolute_path(path: str, root: str = None) -> str:
    if path.startswith('/'):
        path = path[1:]

    if root:
        path = f'{root[:1] if root.startswith("/") else root}/{path}'

    return path


def put_object(bucket_name: str, path: str, data: bytes | RawIOBase, length: int):
    path = get_absolute_path(path)
    s3_client = get_s3_client()

    result = s3_client.put_object(bucket_name, path, data, length)

    return result.location


def get_object_stream(bucket_name: str, path: str):
    path = get_absolute_path(path)
    s3_client = get_s3_client()

    logger.debug(f'Getting object from object storage: {bucket_name}:{path}')

    try:
        response = s3_client.get_object(bucket_name, path)
    except Exception as e:
        logger.exception(f'Failed getting object from object storage: {bucket_name}:{path}', exc_info=e)

        raise

    return response


def get_object(bucket_name: str, path: str) -> bytes:
    response = get_object_stream(bucket_name, path)

    try:
        body = response.read()
    finally:
        response.close()
        response.release_conn()

    logger.debug(f'Loaded object from object storage: {bucket_name}:{path}')

    return body
