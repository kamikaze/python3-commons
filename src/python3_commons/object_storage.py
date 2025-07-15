from __future__ import annotations

import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, AsyncGenerator, Iterable, Mapping, Sequence

import aiobotocore.session
from aiobotocore.response import StreamingBody
from botocore.config import Config

if TYPE_CHECKING:
    from types_aiobotocore_s3.client import S3Client

from python3_commons.conf import S3Settings, s3_settings
from python3_commons.helpers import SingletonMeta

logger = logging.getLogger(__name__)


class ObjectStorage(metaclass=SingletonMeta):
    def __init__(self, settings: S3Settings):
        if not settings.s3_endpoint_url:
            raise ValueError('s3_endpoint_url must be set')

        self._session = aiobotocore.session.get_session()
        self._config = {
            'endpoint_url': settings.s3_endpoint_url,
            'region_name': settings.s3_region_name,
            'aws_access_key_id': settings.s3_access_key_id.get_secret_value(),
            'aws_secret_access_key': settings.s3_secret_access_key.get_secret_value(),
            'use_ssl': settings.s3_secure,
            'verify': settings.s3_cert_verify,
            'config': Config(s3={'addressing_style': settings.s3_addressing_style}, signature_version='s3v4'),
        }

    @asynccontextmanager
    async def get_client(self) -> AsyncGenerator[S3Client, None]:
        async with self._session.create_client('s3', **self._config) as client:
            yield client


def get_absolute_path(path: str) -> str:
    if path.startswith('/'):
        path = path[1:]

    if bucket_root := s3_settings.s3_bucket_root:
        path = f'{bucket_root[:1] if bucket_root.startswith("/") else bucket_root}/{path}'

    return path


async def put_object(bucket_name: str, path: str, data: io.BytesIO, length: int, part_size: int = 0) -> str | None:
    storage = ObjectStorage(s3_settings)

    async with storage.get_client() as s3_client:
        try:
            data.seek(0)

            await s3_client.put_object(Bucket=bucket_name, Key=path, Body=data, ContentLength=length)

            logger.debug(f'Stored object into object storage: {bucket_name}:{path}')

            return f's3://{bucket_name}/{path}'

        except Exception as e:
            logger.error(f'Failed to put object to object storage: {bucket_name}:{path}', exc_info=e)

            raise


@asynccontextmanager
async def get_object_stream(bucket_name: str, path: str) -> AsyncGenerator[StreamingBody]:
    storage = ObjectStorage(s3_settings)

    async with storage.get_client() as s3_client:
        logger.debug(f'Getting object from object storage: {bucket_name}:{path}')

        try:
            response = await s3_client.get_object(Bucket=bucket_name, Key=path)

            async with response['Body'] as stream:
                yield stream
        except Exception as e:
            logger.debug(f'Failed getting object from object storage: {bucket_name}:{path}', exc_info=e)

            raise


async def get_object(bucket_name: str, path: str) -> bytes:
    async with get_object_stream(bucket_name, path) as stream:
        body = await stream.read()

    logger.debug(f'Loaded object from object storage: {bucket_name}:{path}')

    return body


async def list_objects(bucket_name: str, prefix: str, recursive: bool = True) -> AsyncGenerator[Mapping, None]:
    storage = ObjectStorage(s3_settings)

    async with storage.get_client() as s3_client:
        paginator = s3_client.get_paginator('list_objects_v2')

        page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='' if recursive else '/')

        async for page in page_iterator:
            if 'Contents' in page:
                for obj in page['Contents']:
                    yield dict(obj)


async def get_object_streams(
    bucket_name: str, path: str, recursive: bool = True
) -> AsyncGenerator[tuple[str, datetime, StreamingBody], None]:
    async for obj in list_objects(bucket_name, path, recursive):
        object_name = obj['Key']
        last_modified = obj['LastModified']

        async with get_object_stream(bucket_name, path) as stream:
            yield object_name, last_modified, stream


async def get_objects(
    bucket_name: str, path: str, recursive: bool = True
) -> AsyncGenerator[tuple[str, datetime, bytes], None]:
    async for object_name, last_modified, stream in get_object_streams(bucket_name, path, recursive):
        data = await stream.read()

        yield object_name, last_modified, data


async def remove_object(bucket_name: str, object_name: str):
    storage = ObjectStorage(s3_settings)

    async with storage.get_client() as s3_client:
        try:
            await s3_client.delete_object(Bucket=bucket_name, Key=object_name)
            logger.debug(f'Removed object from object storage: {bucket_name}:{object_name}')
        except Exception as e:
            logger.error(f'Failed to remove object from object storage: {bucket_name}:{object_name}', exc_info=e)

            raise


async def remove_objects(
    bucket_name: str, prefix: str = None, object_names: Iterable[str] = None
) -> Sequence[Mapping] | None:
    storage = ObjectStorage(s3_settings)

    async with storage.get_client() as s3_client:
        objects_to_delete = []

        if prefix:
            async for obj in list_objects(bucket_name, prefix, recursive=True):
                objects_to_delete.append({'Key': obj['Key']})
        elif object_names:
            objects_to_delete = [{'Key': name} for name in object_names]
        else:
            return None

        if not objects_to_delete:
            return None

        try:
            errors = []
            # S3 delete_objects can handle up to 1000 objects at once
            chunk_size = 1000

            for i in range(0, len(objects_to_delete), chunk_size):
                chunk = objects_to_delete[i : i + chunk_size]

                response = await s3_client.delete_objects(Bucket=bucket_name, Delete={'Objects': chunk})

                if 'Errors' in response:
                    errors.extend(response['Errors'])

            logger.debug(f'Removed {len(objects_to_delete)} objects from object storage: {bucket_name}')

            return errors if errors else None
        except Exception as e:
            logger.error(f'Failed to remove objects from object storage: {bucket_name}', exc_info=e)

            raise
