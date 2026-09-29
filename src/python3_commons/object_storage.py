from __future__ import annotations

import io
import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlparse

try:
    from object_storage_client import ByteStream, ObjectStorageClient
except ImportError as e:
    msg = 'Install python3-commons[object-storage] to use this feature'
    raise RuntimeError(msg) from e

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Iterable, Mapping, Sequence


logger = logging.getLogger(__name__)
_CLIENT: ObjectStorageClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_client() -> ObjectStorageClient:
    global _CLIENT  # noqa: PLW0603

    if not _CLIENT:
        with _CLIENT_LOCK:
            if not _CLIENT:
                _CLIENT = ObjectStorageClient()

    return _CLIENT


class ObjectStorageStream:
    def __init__(self, stream: ByteStream) -> None:
        self._stream = stream

    async def read(self) -> bytes:
        chunks = []

        while chunk := await self._stream.next():
            chunks.append(chunk)

        return b''.join(chunks)

    async def next(self) -> bytes:
        return await self._stream.next()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        chunk = await self._stream.next()

        if not chunk:
            raise StopAsyncIteration

        return chunk


def build_url(work_path: str, path: str = '') -> str:
    path = path.lstrip('/')

    if path.startswith(('s3://', 'file://', 'gs://', 'az://')):
        return path

    base = work_path.rstrip('/')

    if not base.startswith(('s3://', 'file://', 'gs://', 'az://', '/')):
        base = f's3://{base}'

    if not path:
        return base

    base_no_scheme = base.split('://', 1)[-1].lstrip('/')

    if path.startswith(f'{base_no_scheme}/') or path == base_no_scheme:
        prefix = base[: len(base) - len(base_no_scheme)]

        return f'{prefix}{path}'

    parsed = urlparse(base)
    base_path = parsed.path.lstrip('/')

    if base_path and (path.startswith(f'{base_path}/') or path == base_path):
        prefix_url = f'{parsed.scheme}://{parsed.netloc}' if parsed.scheme else ''

        return f'{prefix_url}/{path}'

    return f'{base}/{path}'


async def put_object(
    work_path: str,
    path: str = '',
    data: io.BytesIO | bytes | None = None,
    length: int = 0,
    part_size: int = 0,
) -> str | None:
    try:
        client = get_client()

        if isinstance(data, io.BytesIO):
            content = data.getvalue()
            data.seek(0)
        elif isinstance(data, bytes):
            content = data
        elif data is None:
            content = b''
        else:
            content = bytes(data)

        url = build_url(work_path, path)
        await client.put_object(url, content)
        logger.debug('Stored object into object storage: %s', url)
    except Exception as e:
        logger.exception('Failed to put object to object storage: %s:%s', work_path, path, exc_info=e)
        raise

    return url


@asynccontextmanager
async def get_object_stream(work_path: str, path: str = '') -> AsyncGenerator[ObjectStorageStream]:
    logger.debug('Getting object stream from object storage: %s:%s', work_path, path)

    try:
        client = get_client()
        url = build_url(work_path, path)
        stream = await client.get_object_stream(url)
        yield ObjectStorageStream(stream)
    except Exception as e:
        logger.exception('Failed getting object from object storage: %s:%s', work_path, path, exc_info=e)
        raise


async def get_object(work_path: str, path: str = '') -> bytes:
    logger.debug('Getting object from object storage: %s:%s', work_path, path)

    try:
        client = get_client()
        url = build_url(work_path, path)
        body = await client.get_object(url)
    except Exception as e:
        logger.exception('Failed getting object from object storage: %s:%s', work_path, path, exc_info=e)
        raise

    return body


async def list_objects(work_path: str, prefix: str = '', *, recursive: bool = True) -> AsyncGenerator[Mapping]:
    client = get_client()
    url = build_url(work_path, prefix)
    items = await client.list_objects(url)
    prefix_clean = prefix.strip('/')

    for item in items:
        if not recursive and '/' in item:
            continue

        rel_key = f'{prefix_clean}/{item}' if prefix_clean else item
        obj_url = build_url(work_path, rel_key)

        try:
            meta = await client.get_object_metadata(obj_url)
            last_modified = (
                datetime.fromisoformat(meta['last_modified']) if meta.get('last_modified') else datetime.now()
            )
            size = meta.get('size_bytes', 0)
            etag = meta.get('e_tag')
        except Exception:
            last_modified = datetime.now()
            size = 0
            etag = None

        yield {
            'Key': rel_key,
            'LastModified': last_modified,
            'Size': size,
            'ETag': etag,
        }


async def get_object_streams(
    work_path: str, path: str = '', *, recursive: bool = True
) -> AsyncGenerator[tuple[str, datetime, ObjectStorageStream]]:
    async for obj in list_objects(work_path, path, recursive=recursive):
        object_name = obj['Key']
        last_modified = obj['LastModified']

        async with get_object_stream(work_path, object_name) as stream:
            yield object_name, last_modified, stream


async def get_objects(
    work_path: str, path: str = '', *, recursive: bool = True
) -> AsyncGenerator[tuple[str, datetime, bytes]]:
    async for object_name, last_modified, stream in get_object_streams(work_path, path, recursive=recursive):
        data = await stream.read()

        yield object_name, last_modified, data


async def remove_object(work_path: str, path: str = '') -> None:
    logger.debug('Removing object from object storage: %s:%s', work_path, path)

    try:
        client = get_client()
        url = build_url(work_path, path)
        await client.delete_object(url)
    except Exception as e:
        logger.exception('Failed to remove object from object storage: %s:%s', work_path, path, exc_info=e)
        raise


async def remove_objects(
    work_path: str, prefix: str | None = None, object_names: Iterable[str] | None = None
) -> Sequence[Mapping] | None:
    if not prefix and not object_names:
        return None

    client = get_client()
    errors: list[Mapping] = []

    if prefix:
        url = build_url(work_path, prefix)

        try:
            await client.delete_objects_by_prefix(url)
            logger.debug('Removed objects by prefix from object storage: %s', url)
        except Exception as e:
            logger.exception('Failed to remove objects by prefix: %s', url, exc_info=e)
            raise
    elif object_names:
        for name in object_names:
            url = build_url(work_path, name)

            try:
                await client.delete_object(url)
            except Exception as e:
                logger.exception('Failed to remove object: %s', url, exc_info=e)
                errors.append({'Key': name, 'Error': str(e)})

    return errors or None
