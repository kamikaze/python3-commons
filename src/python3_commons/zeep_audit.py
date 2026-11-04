import asyncio
import logging
import warnings
from datetime import UTC, datetime
from typing import Any, override
from uuid import uuid4

from python3_commons._extras import require

require('audit', 'lxml', 'zeep')

from lxml import etree
from zeep import AsyncClient, Client
from zeep.plugins import HistoryPlugin, Plugin
from zeep.wsdl.definitions import AbstractOperation

from python3_commons import audit
from python3_commons.conf import get_api_client_settings

__all__ = ['ZeepAuditPlugin', 'get_history_plugin']


logger = logging.getLogger(__name__)

_Element = etree._Element  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


class ZeepAuditPlugin(Plugin):
    """Zeep plugin that stores every envelope through `audit._best_effort_store`.

    Inside a running event loop the writes run as background tasks tracked by this plugin (at most `max_pending`
    at a time; extra records are dropped and logged); `await plugin.drain()` waits for them. `soap_client()` drains
    its plugins on exit. Without a running loop the write runs synchronously, bounded by `audit_timeout`.
    The envelope is serialised as is and never modified.
    """

    def __init__(self, audit_name: str = 'zeep', *, max_pending: int = 100) -> None:
        super().__init__()
        self.audit_name = audit_name
        self.max_pending = max_pending
        # Touched only from the owning event loop
        self._tasks: set[asyncio.Task[None]] = set()

    def _store_audit(self, envelope: _Element, operation: AbstractOperation, direction: str) -> None:
        xml = etree.tostring(envelope, encoding='UTF-8', pretty_print=True)
        now = datetime.now(tz=UTC)
        date_path = now.strftime('%Y/%m/%d')
        timestamp = now.strftime('%H%M%S')
        path = f'{date_path}/{self.audit_name}/{operation.name}/{timestamp}_{str(uuid4())[-12:]}_{direction}.xml'

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._store_bounded(path, xml))

            return

        if len(self._tasks) >= self.max_pending:
            # Same rate-limited WARNING as every other dropped audit record
            audit._warn_rate_limited(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
                path, OverflowError(f'zeep audit backlog full ({len(self._tasks)} pending)')
            )

            return

        task = loop.create_task(self._store(path, xml))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def store_audit_in_s3(self, envelope: _Element, operation: AbstractOperation, direction: str) -> None:
        """Deprecated alias of the internal scheduler; removed in 1.0."""
        warnings.warn(
            'ZeepAuditPlugin.store_audit_in_s3 is deprecated and will be removed in 1.0',
            DeprecationWarning,
            stacklevel=2,
        )
        self._store_audit(envelope, operation, direction)

    @staticmethod
    async def _store(path: str, xml: bytes) -> None:
        try:
            await audit._best_effort_store(path, xml)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        except asyncio.CancelledError:
            logger.warning('zeep audit write cancelled, record dropped: %s', path)

            raise

    @staticmethod
    async def _store_bounded(path: str, xml: bytes) -> None:
        try:
            async with asyncio.timeout(get_api_client_settings().audit_timeout):
                await audit._best_effort_store(path, xml)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        except TimeoutError:
            logger.warning('zeep audit write timed out, record dropped: %s', path)

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait up to `timeout` seconds for pending writes; cancel (and log) whatever is still running."""
        tasks = set(self._tasks)

        if not tasks:
            return

        _, pending = await asyncio.wait(tasks, timeout=timeout)

        if pending:
            logger.warning('zeep audit drain timed out, cancelling %d pending writes', len(pending))

            for task in pending:
                task.cancel()

            await asyncio.gather(*pending, return_exceptions=True)

    @override
    def ingress(
        self, envelope: _Element, http_headers: dict[str, str], operation: AbstractOperation
    ) -> tuple[_Element, dict[str, str]]:
        self._store_audit(envelope, operation, 'ingress')

        return envelope, http_headers

    @override
    def egress(
        self,
        envelope: _Element,
        http_headers: dict[str, str],
        operation: AbstractOperation,
        binding_options: Any,
    ) -> tuple[_Element, dict[str, str]]:
        self._store_audit(envelope, operation, 'egress')

        return envelope, http_headers


def get_history_plugin(client: AsyncClient | Client) -> HistoryPlugin | None:
    """Return the first HistoryPlugin attached to *client*, or None."""
    return next(
        (p for p in client.plugins if isinstance(p, HistoryPlugin)),
        None,
    )
