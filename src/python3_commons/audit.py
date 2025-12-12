import asyncio
import io
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from lxml import etree
from zeep.plugins import Plugin

from python3_commons import object_storage
from python3_commons.conf import S3Settings, s3_settings

if TYPE_CHECKING:
    from zeep.wsdl.definitions import AbstractOperation

logger = logging.getLogger(__name__)


async def write_audit_data(settings: S3Settings, key: str, data: bytes) -> None:
    if settings.aws_secret_access_key:
        try:
            absolute_path = object_storage.get_absolute_path(f'audit/{key}')

            await object_storage.put_object(settings.s3_bucket, absolute_path, io.BytesIO(data), len(data))
        except Exception:
            logger.exception('Failed storing object in storage.')
        else:
            logger.debug('Stored object in storage: %s', key)
    else:
        logger.debug('S3 is not configured, not storing object in storage: %s', key)


class ZeepAuditPlugin(Plugin):
    def __init__(self, audit_name: str = 'zeep') -> None:
        super().__init__()
        self.audit_name = audit_name

    def store_audit_in_s3(self, envelope, operation: AbstractOperation, direction: str) -> None:
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
