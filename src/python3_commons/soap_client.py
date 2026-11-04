import asyncio
import logging
import ssl
import sys
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from python3_commons._extras import require

require('soap-client', 'aiohttp', 'certifi', 'requests', 'zeep')

import aiohttp
import certifi
from aiohttp import ClientSession, ClientTimeout, TCPConnector
from requests import Response
from requests.cookies import RequestsCookieJar
from requests.structures import CaseInsensitiveDict
from zeep import AsyncClient
from zeep.plugins import Plugin
from zeep.transports import Transport
from zeep.utils import get_version
from zeep.wsdl.utils import etree_to_string

if TYPE_CHECKING:
    # zeep_audit needs the `audit` extra, which `soap-client` does not install
    from python3_commons.zeep_audit import ZeepAuditPlugin

__all__ = [
    'AsyncTransport',
    'TransportConfig',
    'abuild_soap_client',
    'build_soap_client',
    'soap_client',
]
logger = logging.getLogger(__name__)


def _make_ssl_context(*, verify: bool) -> ssl.SSLContext | bool:
    if not verify:
        return False

    return ssl.create_default_context(cafile=certifi.where())


def _new_session(config: TransportConfig, timeout: int) -> ClientSession:
    return ClientSession(
        connector=TCPConnector(ssl=_make_ssl_context(verify=config.verify_ssl)),
        timeout=ClientTimeout(total=timeout),
        headers={'User-Agent': f'Zeep/{get_version()} (www.python-zeep.org)'},
    )


@dataclass(frozen=True, slots=True)
class TransportConfig:
    timeout: int = 300
    operation_timeout: int = 60
    verify_ssl: bool = True
    proxy: str | None = None


# -------------------------
# Transport
# -------------------------


class AsyncTransport(Transport):
    def __init__(
        self,
        *,
        session: ClientSession,
        config: TransportConfig,
        owns_session: bool = False,
    ) -> None:
        super().__init__(timeout=config.timeout, operation_timeout=config.operation_timeout)
        self.session.verify = config.verify_ssl

        if config.proxy:
            self.session.proxies = {'http': config.proxy, 'https': config.proxy}

        self._session = session
        self._config = config
        self._owns_session = owns_session

    @classmethod
    def from_config(
        cls,
        config: TransportConfig | None = None,
        *,
        session: ClientSession | None = None,
    ) -> AsyncTransport:
        config = config or TransportConfig()

        if session is None:
            return cls(session=_new_session(config, config.operation_timeout), config=config, owns_session=True)

        return cls(session=session, config=config)

    async def aclose(self) -> None:
        # The requests session is always created by this transport (WSDL loading), so it is always closed.
        self.session.close()

        if self._owns_session:
            await self._session.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _build_response(response: aiohttp.ClientResponse, body: bytes) -> Response:
        r = Response()
        r.status_code = response.status
        r._content = body  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        headers: CaseInsensitiveDict[str] = CaseInsensitiveDict()

        for key, value in response.headers.items():
            headers[key] = f'{headers[key]}, {value}' if key in headers else value

        r.headers = headers
        r.encoding = response.charset
        r.url = str(response.url)

        jar = RequestsCookieJar()

        for name, morsel in response.cookies.items():
            jar.set(name, morsel.value)

        r.cookies = jar

        return r

    async def post_xml(
        self,
        address: str,
        envelope: Any,
        headers: dict[str, str],
    ) -> Response:
        return await self.post(address, etree_to_string(envelope), headers)

    async def post(
        self,
        address: str,
        message: bytes,
        headers: dict[str, str],
        *,
        timeout: int | None = None,
    ) -> Response:
        logger.debug('SOAP POST → %s', address)

        effective = timeout if timeout is not None else self.operation_timeout
        kwargs: dict[str, Any] = {} if effective is None else {'timeout': ClientTimeout(total=effective)}

        async with self._session.post(
            address,
            data=message,
            headers=headers,
            proxy=self._config.proxy,
            **kwargs,
        ) as resp:
            body = await resp.read()
            logger.debug('SOAP ← %s (%d)', address, resp.status)
            return self._build_response(resp, body)

    async def get(
        self,
        address: str,
        params: dict[str, str],
        headers: dict[str, str],
        *,
        timeout: int | None = None,
    ) -> Response:
        effective = timeout if timeout is not None else self.operation_timeout
        kwargs: dict[str, Any] = {} if effective is None else {'timeout': ClientTimeout(total=effective)}

        async with self._session.get(
            address,
            params=params,
            headers=headers,
            proxy=self._config.proxy,
            **kwargs,
        ) as resp:
            body = await resp.read()
            return self._build_response(resp, body)


def build_soap_client(
    wsdl_url: str,
    transport: AsyncTransport,
    plugins: Sequence[Plugin] | None = None,
) -> AsyncClient:
    """
    Build a zeep AsyncClient.

    This function BLOCKS: zeep loads and parses the WSDL synchronously through the requests-based
    transport. Never call it from a running event loop; use :func:`abuild_soap_client` instead.
    """
    if not wsdl_url:
        msg = 'wsdl_url must be a non-empty string.'

        raise ValueError(msg)

    return AsyncClient(
        wsdl_url,
        transport=transport,
        plugins=list(plugins or []),
    )


async def abuild_soap_client(
    wsdl_url: str,
    transport: AsyncTransport,
    plugins: Sequence[Plugin] | None = (),
) -> AsyncClient:
    """Build a zeep AsyncClient in a worker thread so the event loop is not blocked by WSDL loading."""
    return await asyncio.to_thread(build_soap_client, wsdl_url, transport, plugins)


async def _drain_audit_plugins(plugins: Sequence[Plugin] | None) -> None:
    """Wait for pending background audit writes of any ZeepAuditPlugin in *plugins*."""
    # Imported lazily: zeep_audit needs the optional `audit` extra
    if not plugins or 'python3_commons.zeep_audit' not in sys.modules:
        return

    plugin_type: type[ZeepAuditPlugin] = sys.modules['python3_commons.zeep_audit'].ZeepAuditPlugin

    for plugin in plugins:
        if isinstance(plugin, plugin_type):
            await plugin.drain()


@asynccontextmanager
async def soap_client(
    wsdl_url: str,
    *,
    config: TransportConfig | None = None,
    session: ClientSession | None = None,
    plugins: Sequence[Plugin] | None = None,
) -> AsyncGenerator[AsyncClient]:
    """
    Async context manager yielding a ready-to-use zeep AsyncClient.

    Example::

        async with soap_client("https://example.com/service?wsdl") as client:
            result = await client.service.GetData(id=42)
    """
    transport = AsyncTransport.from_config(config, session=session)

    try:
        yield await abuild_soap_client(wsdl_url, transport, plugins)
    finally:
        try:
            await _drain_audit_plugins(plugins)
        finally:
            await transport.aclose()
