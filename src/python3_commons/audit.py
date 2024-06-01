import asyncio
import io
import logging
import tarfile
from datetime import datetime, timedelta, UTC
from io import BytesIO
from typing import Generator
from uuid import uuid4

from lxml import etree
from minio import S3Error
from zeep.plugins import Plugin
from zeep.wsdl.definitions import AbstractOperation

from python3_commons import object_storage
from python3_commons.conf import S3Settings, s3_settings
from python3_commons.object_storage import get_s3_client

logger = logging.getLogger(__name__)


class BytesIOStream(io.BytesIO):
    def __init__(self, generator: Generator[bytes, None, None], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.generator = generator

    def read(self, size: int = -1):
        if size == -1:
            size = 4096

        while self.tell() < size:
            try:
                chunk = next(self.generator)
            except StopIteration:
                break

            self.write(chunk)

        if chunk := self.read(size):
            pos = self.tell()

            buf = self.getbuffer()
            unread_data_size = len(buf) - pos

            if unread_data_size > 0:
                buf[:unread_data_size] = buf[pos:pos+unread_data_size]

            self.truncate(unread_data_size)
            self.seek(0)

        return chunk

    def readable(self):
        return True


def generate_archive(bucket_name: str, date_path: str, chunk_size: int = 4096) -> Generator[bytes, None, None]:
    buffer = io.BytesIO()

    with tarfile.open(fileobj=buffer, mode='w|bz2') as archive:
        objects = object_storage.get_objects(bucket_name, date_path, recursive=True)

        if objects:
            logger.info(f'Compacting files in: {date_path}')

            for name, last_modified, content in objects:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mtime = last_modified.timestamp()
                archive.addfile(info, io.BytesIO(content))
                buffer.seek(0)

                while True:
                    chunk = buffer.read(chunk_size)

                    if not chunk:
                        break

                    yield chunk

                buffer.seek(0)
                buffer.truncate(0)


def write_audit_data_sync(settings: S3Settings, key: str, data: bytes):
    if settings.s3_secret_access_key:
        try:
            client = get_s3_client(settings)
            absolute_path = object_storage.get_absolute_path(f'audit/{key}')

            client.put_object(settings.s3_bucket, absolute_path, io.BytesIO(data), len(data))
        except S3Error as e:
            logger.error(f'Failed storing object in storage: {e}')
        else:
            logger.debug(f'Stored object in storage: {key}')
    else:
        logger.debug(f'S3 is not configured, not storing object in storage: {key}')


async def write_audit_data(settings: S3Settings, key: str, data: bytes):
    write_audit_data_sync(settings, key, data)


async def archive_audit_data(root_path: str = 'audit'):
    now = datetime.now(tz=UTC) - timedelta(days=1)
    year = now.year
    month = now.month
    day = now.day
    bucket_name = s3_settings.s3_bucket
    fo = BytesIO()
    object_names = []
    date_path = object_storage.get_absolute_path(f'{root_path}/{year}/{month:02}/{day:02}')

    generator = generate_archive(bucket_name, date_path, chunk_size=4096)
    archive_stream = BytesIOStream(generator)

    if object_names:
        archive_path = object_storage.get_absolute_path(f'audit/.archive/{year}_{month:02}_{day:02}.tar.bz2')
        object_storage.put_object(bucket_name, archive_path, archive_stream, -1, part_size=4096)

        if errors := object_storage.remove_objects(bucket_name, object_names=object_names):
            for error in errors:
                logger.error(f'Failed to delete object in {bucket_name=}: {error}')
    else:
        logger.info('No objects to archive found.')


class ZeepAuditPlugin(Plugin):
    def __init__(self, audit_name: str = 'zeep'):
        super().__init__()
        self.audit_name = audit_name

    def store_audit_in_s3(self, envelope, operation: AbstractOperation, direction: str):
        xml = etree.tostring(envelope, encoding='UTF-8', pretty_print=True)
        now = datetime.now(tz=UTC)
        date_path = now.strftime('%Y/%m/%d')
        timestamp = now.strftime('%H%M%S')
        path = f'{date_path}/{self.audit_name}/{operation.name}/{timestamp}_{str(uuid4())[-12:]}_{direction}.xml'
        coro = write_audit_data(s3_settings, path, xml)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            loop.create_task(coro)
        else:
            asyncio.run(coro)

    def ingress(self, envelope, http_headers, operation: AbstractOperation):
        self.store_audit_in_s3(envelope, operation, 'ingress')

        return envelope, http_headers

    def egress(self, envelope, http_headers, operation: AbstractOperation, binding_options):
        self.store_audit_in_s3(envelope, operation, 'egress')

        return envelope, http_headers
