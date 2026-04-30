import io
import logging
from typing import TYPE_CHECKING

from python3_commons import object_storage

if TYPE_CHECKING:
    from python3_commons.conf import S3Settings

logger = logging.getLogger(__name__)


async def write_audit_data(settings: S3Settings, key: str, data: bytes) -> None:
    if settings.aws_secret_access_key:
        try:
            await object_storage.put_object(settings.s3_bucket, f'audit/{key}', io.BytesIO(data), len(data))
        except Exception:
            logger.exception('Failed storing object in storage.')
        else:
            logger.debug('Stored object in storage: %s', key)
    else:
        logger.debug('S3 is not configured, not storing object in storage: %s', key)
