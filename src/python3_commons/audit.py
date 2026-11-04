import asyncio
import importlib
import io
import logging
import time
import warnings
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from python3_commons import _paths
from python3_commons.conf import _S3Settings, get_api_client_settings  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    # `X as X` marks an explicit re-export for type checkers; at runtime __getattr__ serves the names
    from python3_commons.zeep_audit import ZeepAuditPlugin as ZeepAuditPlugin  # noqa: PLC0414
    from python3_commons.zeep_audit import get_history_plugin as get_history_plugin  # noqa: PLC0414

# Zeep plugins live in python3_commons.zeep_audit: importing audit (api_client does) does not load zeep and lxml
ZEEP_NAMES = frozenset({'ZeepAuditPlugin', 'get_history_plugin'})

# The zeep names stay importable by name (module __getattr__) but are left out of __all__:
# `from python3_commons.audit import *` resolves every __all__ name and would import zeep
__all__ = ['normalize_audit_root', 'store_audit', 'write_audit_data', 'write_audit_data_soon']

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()

_WARNING_INTERVAL = 60.0
_last_warning: float | None = None
_suppressed = 0


normalize_audit_root = _paths.normalize_root


async def store_audit(key: str, data: bytes, *, root: str | None = None) -> None:
    """Store one audit record under `root` (default: `API_CLIENT_AUDIT_PATH`); the single audit write path.

    Raising: storage errors propagate, so callers choose their policy (`write_audit_data` logs and swallows,
    `_best_effort_store` drops with a rate-limited warning). A blank root means "audit off" and is a no-op.
    """
    root = root or get_api_client_settings().audit_path

    if not root or not root.strip():
        logger.debug('audit storage not configured, not storing object: %s', key)

        return

    segments = [segment for segment in key.split('/') if segment]

    if any(unquote(segment) in {'.', '..'} for segment in segments):
        logger.warning('audit key has a "." or ".." segment, not storing object: %r', key)

        return

    # imported here: audit with no root never needs the object-storage extra; a missing extra raises RuntimeError
    from python3_commons import object_storage  # noqa: PLC0415

    await object_storage.put_object(
        work_path=_paths.normalize_root(root), path='/'.join(segments), data=io.BytesIO(data)
    )


async def write_audit_data(
    settings: _S3Settings | None = None, key: str = '', data: bytes = b'', *, root: str | None = None
) -> None:
    """Best-effort `store_audit`: failures are logged, never raised. `settings` is deprecated and ignored."""
    if settings is not None:
        warnings.warn(
            'the settings argument of write_audit_data() is ignored and will be removed in 1.0',
            DeprecationWarning,
            stacklevel=2,
        )

    try:
        await store_audit(key, data, root=root)
    except Exception:
        logger.exception('Failed storing object in storage.')
    else:
        logger.debug('Stored object in storage: %s', key)


def _warn_rate_limited(key: str, exc: BaseException) -> None:
    """Log at most one WARNING per 60 s per process; the next one reports how many were suppressed."""
    global _last_warning, _suppressed  # noqa: PLW0603
    now = time.monotonic()

    if _last_warning is not None and now - _last_warning < _WARNING_INTERVAL:
        _suppressed += 1

        return

    suppressed, _suppressed, _last_warning = _suppressed, 0, now
    suffix = f' ({suppressed} similar warnings suppressed)' if suppressed else ''
    logger.warning('audit write failed, record dropped: %s: %r%s', key, exc, suffix)


async def _best_effort_store(key: str, data: bytes, *, timeout: float | None = None) -> None:
    """Store an audit record without ever failing or stalling the caller.

    Bounded by `timeout` (default: `ApiClientSettings.audit_timeout`). Any `Exception`, a timeout included, is
    dropped with a rate-limited WARNING; `CancelledError` and other `BaseException`s propagate.
    """
    if timeout is None:
        timeout = get_api_client_settings().audit_timeout

    try:
        async with asyncio.timeout(timeout):
            await store_audit(key, data)
    except Exception as e:  # noqa: BLE001  # best-effort store: audit never fails the request it audits
        _warn_rate_limited(key, e)


def write_audit_data_soon(key: str, data: bytes) -> None:
    """Store audit data from synchronous code (zeep plugins): in background when an event loop runs"""
    coro = write_audit_data(key=key, data=data)

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
