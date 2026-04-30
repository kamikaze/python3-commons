"""
Async SOAP client built on aiohttp + zeep.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self
from uuid import uuid4

try:
    import aiohttp
    from aiohttp import ClientSession, ClientTimeout, TCPConnector
    from lxml import etree
    from requests import Response
    from requests.cookies import RequestsCookieJar
    from zeep import AsyncClient
    from zeep.exceptions import TransportError
    from zeep.plugins import HistoryPlugin, Plugin
    from zeep.transports import Transport
    from zeep.utils import get_version
    from zeep.wsdl.utils import etree_to_string
except ImportError as e:
    msg = 'Install python3-commons[soap-client] to use this feature'

    raise RuntimeError(msg) from e

from python3_commons.audit import write_audit_data
from python3_commons.conf import s3_settings

if TYPE_CHECKING:
    from zeep.wsdl.definitions import AbstractOperation

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class TransportConfig:
    """Immutable transport settings passed to AsyncTransport."""

    timeout: int = 300
    """Total timeout in seconds for WSDL fetches."""

    operation_timeout: int = 60
    """Total timeout in seconds for SOAP operation calls."""

    verify_ssl: bool = True
    proxy: str | None = None


class AsyncTransport(Transport):
    """
    Async transport for zeep using aiohttp.

    Usage::

        async with AsyncTransport.from_config(config) as transport:
            client = AsyncClient(wsdl_url, transport=transport)
            result = await client.service.SomeOperation(...)
    """

    def __init__(
        self,
        *,
        session: ClientSession,
        wsdl_session: ClientSession,
        config: TransportConfig,
        _owns_session: bool = False,
        _owns_wsdl_session: bool = False,
    ) -> None:
        super().__init__()
        self._session = session
        self._wsdl_session = wsdl_session
        self._config = config
        self._owns_session = _owns_session
        self._owns_wsdl_session = _owns_wsdl_session

    @classmethod
    def from_config(
        cls,
        config: TransportConfig | None = None,
        *,
        session: ClientSession | None = None,
        wsdl_session: ClientSession | None = None,
    ) -> AsyncTransport:
        """
        Create a transport, optionally sharing an existing ClientSession.

        If *session* / *wsdl_session* are omitted the transport owns (and
        will close) the sessions it creates.
        """
        config = config or TransportConfig()
        connector = TCPConnector(ssl=config.verify_ssl)
        user_agent = f'Zeep/{get_version()} (www.python-zeep.org)'

        owns_session = session is None
        owns_wsdl_session = wsdl_session is None

        if owns_session:
            session = ClientSession(
                connector=connector,
                timeout=ClientTimeout(total=config.operation_timeout),
                headers={'User-Agent': user_agent},
            )

        if owns_wsdl_session:
            wsdl_session = ClientSession(
                connector=connector,
                timeout=ClientTimeout(total=config.timeout),
                headers={'User-Agent': user_agent},
            )

        return cls(
            session=session,
            wsdl_session=wsdl_session,
            config=config,
            _owns_session=owns_session,
            _owns_wsdl_session=owns_wsdl_session,
        )

    async def aclose(self) -> None:
        if self._owns_session:
            await self._session.close()
        if self._owns_wsdl_session:
            await self._wsdl_session.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _build_response(response: aiohttp.ClientResponse, body: bytes) -> Response:
        """Convert an aiohttp response into a requests.Response for zeep."""
        r = Response()
        r.status_code = response.status
        r._content = body  # noqa: SLF001  (zeep reads this attribute directly)
        r.headers = dict(response.headers)
        r.encoding = response.charset
        r.url = str(response.url)

        # Bridge aiohttp SimpleCookie → RequestsCookieJar so zeep / requests
        # cookie handling works correctly.
        jar = RequestsCookieJar()

        for name, morsel in response.cookies.items():
            jar.set(name, morsel.value)

        r.cookies = jar

        return r

    async def _load_remote_data(self, url: str) -> bytes:
        """Fetch WSDL / XSD documents (called by zeep during init)."""

        async def _fetch() -> bytes:
            async with self._wsdl_session.get(url, proxy=self._config.proxy) as resp:
                content = await resp.read()

                if resp.status >= 400:
                    raise TransportError(
                        status_code=resp.status,
                        message=content.decode(errors='ignore'),
                    )

                return content

        return await _fetch()

    async def post(
        self,
        address: str,
        message: bytes,
        headers: dict[str, str],
        *,
        timeout: int | None = None,
    ) -> Response:
        logger.debug('SOAP POST → %s\n%s', address, message)

        request_timeout = ClientTimeout(total=timeout) if timeout is not None else None

        async def _post() -> Response:
            async with self._session.post(
                address,
                data=message,
                headers=headers,
                proxy=self._config.proxy,
                timeout=request_timeout,
            ) as resp:
                body = await resp.read()
                logger.debug('SOAP ← %s (HTTP %d)\n%s', address, resp.status, body)

                return self._build_response(resp, body)

        return await _post()

    async def post_xml(
        self,
        address: str,
        envelope: Any,
        headers: dict[str, str],
    ) -> Response:
        message = etree_to_string(envelope)

        return await self.post(address, message, headers)

    async def get(
        self,
        address: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> Response:
        async with self._session.get(
            address,
            params=params,
            headers=headers,
            proxy=self._config.proxy,
        ) as resp:
            body = await resp.read()

            return self._build_response(resp, body)


def build_soap_client(
    wsdl_url: str,
    transport: AsyncTransport,
    plugins: Sequence[Plugin] | None = None,
) -> AsyncClient:
    """
    Construct a zeep AsyncClient with the supplied transport and plugins.

    Raises ValueError if *wsdl_url* is empty or None.
    """
    if not wsdl_url:
        msg = 'wsdl_url must be a non-empty string.'

        raise ValueError(msg)

    return AsyncClient(wsdl_url, transport=transport, plugins=list(plugins or []))


@asynccontextmanager
async def soap_client(
    wsdl_url: str,
    *,
    config: TransportConfig | None = None,
    session: ClientSession | None = None,
    plugins: Sequence[Plugin] | None = None,
) -> AsyncIterator[AsyncClient]:
    """
    Async context manager that yields a ready-to-use zeep AsyncClient and
    cleans up the transport on exit.

    Example::

        async with soap_client('https://example.com/service?wsdl') as client:
            result = await client.service.GetData(id=42)
    """
    transport = AsyncTransport.from_config(config, session=session)

    async with transport:
        yield build_soap_client(wsdl_url, transport, plugins)


def get_history_plugin(client: AsyncClient) -> HistoryPlugin | None:
    """Return the first HistoryPlugin attached to *client*, or None."""
    return next(
        (p for p in client.plugins if isinstance(p, HistoryPlugin)),
        None,
    )
