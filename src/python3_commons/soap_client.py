"""
Async SOAP client built on aiohttp + zeep.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

import certifi

try:
    import aiohttp
    from aiohttp import ClientSession, ClientTimeout, TCPConnector
    from requests import Response
    from requests.cookies import RequestsCookieJar
    from zeep import AsyncClient
    from zeep.exceptions import TransportError
    from zeep.transports import Transport
    from zeep.utils import get_version
    from zeep.wsdl.utils import etree_to_string
except ImportError as e:
    msg = 'Install python3-commons[soap-client] to use this feature'

    raise RuntimeError(msg) from e

if TYPE_CHECKING:
    from zeep.plugins import Plugin

logger = logging.getLogger(__name__)


def _make_ssl_context(*, verify: bool) -> ssl.SSLContext | bool:
    if not verify:
        return False  # aiohttp accepts False to disable verification

    return ssl.create_default_context(cafile=certifi.where())


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

        async with soap_client("https://example.com/service?wsdl") as client:
            result = await client.service.SomeOperation(...)
    """

    def __init__(
        self,
        *,
        session: ClientSession,
        config: TransportConfig,
        _owns_session: bool = False,
    ) -> None:
        super().__init__()
        self._session = session
        self._config = config
        self._owns_session = _owns_session

    @classmethod
    def from_config(
        cls,
        config: TransportConfig | None = None,
        *,
        session: ClientSession | None = None,
    ) -> Self:
        config = config or TransportConfig()
        owns_session = session is None

        if owns_session:
            session = ClientSession(
                connector=TCPConnector(ssl=_make_ssl_context(verify=config.verify_ssl)),
                timeout=ClientTimeout(total=config.operation_timeout),
                headers={'User-Agent': f'Zeep/{get_version()} (www.python-zeep.org)'},
            )

        return cls(session=session, config=config, _owns_session=owns_session)

    async def aclose(self) -> None:
        if self._owns_session:
            await self._session.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _build_response(response: aiohttp.ClientResponse, body: bytes) -> Response:
        """Convert an aiohttp response into a requests.Response for zeep."""
        r = Response()
        r.status_code = response.status
        r._content = body  # noqa: SLF001
        r.headers = dict(response.headers)
        r.encoding = response.charset
        r.url = str(response.url)

        jar = RequestsCookieJar()

        for name, morsel in response.cookies.items():
            jar.set(name, morsel.value)

        r.cookies = jar

        return r

    def load(self, url: str) -> bytes:
        """
        Sync entry-point zeep calls during WSDL document init.

        Creates a short-lived session confined to its own event loop so there
        is no cross-loop session sharing with the operational session.
        """
        if not url:
            return b''

        async def _fetch() -> bytes:
            async with (
                ClientSession(
                    connector=TCPConnector(ssl=_make_ssl_context(verify=self._config.verify_ssl)),
                    timeout=ClientTimeout(total=self._config.timeout),
                    headers={'User-Agent': f'Zeep/{get_version()} (www.python-zeep.org)'},
                ) as session,
                session.get(url, proxy=self._config.proxy) as resp,
            ):
                content = await resp.read()

                if resp.status >= 400:
                    raise TransportError(
                        status_code=resp.status,
                        message=content.decode(errors='ignore'),
                    )

                return content

        # load() is always called from within a running event loop (during
        # AsyncClient.__init__ inside the soap_client context manager), so
        # run_until_complete on the running loop would raise. Always delegate
        # to a thread with its own fresh event loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, _fetch()).result()


async def post(
    self,
    address: str,
    message: bytes,
    headers: dict[str, str],
    *,
    timeout: int | None = None,
) -> Response:
    logger.debug('SOAP POST → %s\n%s', address, message)

    async with self._session.post(
        address,
        data=message,
        headers=headers,
        proxy=self._config.proxy,
        timeout=ClientTimeout(total=timeout) if timeout is not None else None,
    ) as resp:
        body = await resp.read()
        logger.debug('SOAP ← %s (HTTP %d)\n%s', address, resp.status, body)

        return self._build_response(resp, body)


async def post_xml(
    self,
    address: str,
    envelope: Any,
    headers: dict[str, str],
) -> Response:
    return await self.post(address, etree_to_string(envelope), headers)


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
    Async context manager yielding a ready-to-use zeep AsyncClient.

    Example::

        async with soap_client("https://example.com/service?wsdl") as client:
            result = await client.service.GetData(id=42)
    """
    transport = AsyncTransport.from_config(config, session=session)

    try:
        yield build_soap_client(wsdl_url, transport, plugins)
    finally:
        await transport.aclose()
