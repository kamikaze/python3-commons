import asyncio
import importlib
import io
import logging
from typing import TYPE_CHECKING, Any

from python3_commons import object_storage
from python3_commons.conf import api_client_settings, object_storage_settings, s3_settings

if TYPE_CHECKING:
    from python3_commons.conf import S3Settings
    from python3_commons.zeep_audit import ZeepAuditPlugin, get_history_plugin

# Zeep plugins live in python3_commons.zeep_audit: importing audit (api_client does) does not load zeep and lxml
ZEEP_NAMES = frozenset({'ZeepAuditPlugin', 'get_history_plugin'})

__all__ = ['ZeepAuditPlugin', 'get_history_plugin', 'write_audit_data', 'write_audit_data_soon']

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


async def write_audit_data(settings: S3Settings = s3_settings, key: str = '', data: bytes = b'') -> None:
    if settings.secret_access_key or api_client_settings.audit_path or object_storage_settings.work_path:
        clean_key = key.lstrip('/')

        if api_client_settings.audit_path:
            work_path = api_client_settings.audit_path
            path = clean_key
        elif object_storage_settings.work_path:
            work_path = object_storage_settings.work_path
            base = work_path.rstrip('/')
            already_audit_scoped = (
                key.startswith(base) or base.rsplit('/', 1)[-1] == 'audit' or clean_key.startswith('audit/')
            )
            path = clean_key if already_audit_scoped else f'audit/{clean_key}'
        else:
            work_path = 'audit'
            path = clean_key

        try:
            await object_storage.put_object(work_path=work_path, path=path, data=io.BytesIO(data), length=len(data))
        except Exception:
            logger.exception('Failed storing object in storage.')
        else:
            logger.debug('Stored object in storage: %s', key)
    else:
        logger.debug('Storage is not configured, not storing object in storage: %s', key)


def write_audit_data_soon(key: str, data: bytes) -> None:
    """Store audit data from synchronous code (zeep plugins): in background when an event loop runs"""
    coro = write_audit_data(s3_settings, key, data)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
    else:
        task = loop.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)


def __getattr__(name: str) -> Any:
    """`from python3_commons.audit import ZeepAuditPlugin` keeps working: zeep is loaded only then"""
    if name in ZEEP_NAMES:
        return getattr(importlib.import_module('python3_commons.zeep_audit'), name)

    msg = f'module {__name__!r} has no attribute {name!r}'

    raise AttributeError(msg)
