from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

try:
    import aiohttp
    import certifi
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
        return False

    return ssl.create_default_context(cafile=certifi.where())


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
        loop: asyncio.AbstractEventLoop,
        owns_session: bool = False,
    ) -> None:
        super().__init__()
        self._session = session
        self._config = config
        self._loop = loop
        self._owns_session = owns_session

    @classmethod
    def from_config(
        cls,
        config: TransportConfig | None = None,
        *,
        session: ClientSession | None = None,
    ) -> AsyncTransport:
        config = config or TransportConfig()
        loop = asyncio.get_running_loop()
        owns_session = session is None

        if owns_session:
            session = ClientSession(
                connector=TCPConnector(ssl=_make_ssl_context(verify=config.verify_ssl)),
                timeout=ClientTimeout(total=config.operation_timeout),
                headers={'User-Agent': f'Zeep/{get_version()} (www.python-zeep.org)'},
            )

        return cls(
            session=session,
            config=config,
            loop=loop,
            owns_session=owns_session,
        )

    async def aclose(self) -> None:
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
        r._content = body  # noqa: SLF001
        r.headers = dict(response.headers)
        r.encoding = response.charset
        r.url = str(response.url)

        jar = RequestsCookieJar()

        for name, morsel in response.cookies.items():
            jar.set(name, morsel.value)

        r.cookies = jar

        return r

    async def _fetch(self, url: str) -> bytes:
        async with self._session.get(
            url,
            proxy=self._config.proxy,
            timeout=ClientTimeout(total=self._config.timeout),
        ) as resp:
            body = await resp.read()

            if resp.status >= 400:
                raise TransportError(
                    status_code=resp.status,
                    message=body.decode(errors='ignore'),
                )

            return body

    def load(self, url: str) -> bytes:
        """
        Called synchronously by zeep during WSDL parsing.

        We safely hop into the main loop.
        """
        if not url:
            return b''

        future = asyncio.run_coroutine_threadsafe(
            self._fetch(url),
            self._loop,
        )

        return future.result()

    def post_xml(
        self,
        address: str,
        envelope: Any,
        headers: dict[str, str],
    ) -> Response:
        future = asyncio.run_coroutine_threadsafe(
            self.post(address, etree_to_string(envelope), headers),
            self._loop,
        )

        return future.result()

    async def post(
        self,
        address: str,
        message: bytes,
        headers: dict[str, str],
        *,
        timeout: int | None = None,
    ) -> Response:
        logger.debug('SOAP POST → %s', address)

        async with self._session.post(
            address,
            data=message,
            headers=headers,
            proxy=self._config.proxy,
            timeout=ClientTimeout(total=timeout) if timeout is not None else None,
        ) as resp:
            body = await resp.read()
            logger.debug('SOAP ← %s (%d)', address, resp.status)
            return self._build_response(resp, body)

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

    return AsyncClient(
        wsdl_url,
        transport=transport,
        plugins=list(plugins or []),
    )


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
