from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

try:
    from lxml import etree
    from zeep.plugins import HistoryPlugin, Plugin
except ImportError as e:
    msg = 'Install python3-commons[audit] to use this feature'

    raise RuntimeError(msg) from e

from python3_commons import audit

if TYPE_CHECKING:
    from zeep import AsyncClient
    from zeep.wsdl.definitions import AbstractOperation


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
        audit.write_audit_data_soon(path, xml)

    def ingress(self, envelope, http_headers, operation: AbstractOperation):
        self.store_audit_in_s3(envelope, operation, 'ingress')

        return envelope, http_headers

    def egress(self, envelope, http_headers, operation: AbstractOperation, binding_options):
        self.store_audit_in_s3(envelope, operation, 'egress')

        return envelope, http_headers


def get_history_plugin(client: AsyncClient) -> HistoryPlugin | None:
    """Return the first HistoryPlugin attached to *client*, or None."""
    return next(
        (p for p in client.plugins if isinstance(p, HistoryPlugin)),
        None,
    )
