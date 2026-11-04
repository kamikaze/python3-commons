import asyncio
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from lxml import etree
from multidict import CIMultiDict, CIMultiDictProxy
from pytest_mock import MockerFixture

from python3_commons import soap_client as soap_client_module
from python3_commons import zeep_audit
from python3_commons.conf import get_api_client_settings
from python3_commons.soap_client import AsyncTransport, TransportConfig, abuild_soap_client, soap_client

pytestmark = pytest.mark.asyncio

WSDL = b"""<?xml version="1.0"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
  xmlns:tns="urn:t" xmlns:xsd="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:t" name="T">
  <message name="EchoIn"><part name="v" type="xsd:string"/></message>
  <message name="EchoOut"><part name="r" type="xsd:string"/></message>
  <portType name="P"><operation name="Echo">
    <input message="tns:EchoIn"/><output message="tns:EchoOut"/></operation></portType>
  <binding name="B" type="tns:P"><soap:binding style="rpc" transport="http://schemas.xmlsoap.org/soap/http"/>
    <operation name="Echo"><soap:operation soapAction="Echo"/>
      <input><soap:body use="literal" namespace="urn:t"/></input>
      <output><soap:body use="literal" namespace="urn:t"/></output>
    </operation></binding>
  <service name="S"><port name="SP" binding="tns:B"><soap:address location="http://127.0.0.1:1/x"/></port></service>
</definitions>"""


@pytest_asyncio.fixture
async def server():
    async def slow_wsdl(request: web.Request) -> web.Response:
        await asyncio.sleep(1.0)

        return web.Response(body=WSDL, content_type='text/xml')

    async def slow_post(request: web.Request) -> web.Response:
        await asyncio.sleep(1.0)

        return web.Response(text='late')

    app = web.Application()
    app.router.add_get('/svc', slow_wsdl)
    app.router.add_post('/op', slow_post)
    srv = TestServer(app)
    await srv.start_server()

    yield srv

    await srv.close()


async def test_wsdl_load_does_not_block_loop(server: TestServer):
    gaps: list[float] = []
    stop = False

    async def ticker() -> None:
        last = time.monotonic()

        while not stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)

    # the WSDL is served from this same loop, so a blocking build would deadlock until the timeout
    async with soap_client(str(server.make_url('/svc'))) as client:
        assert 'Echo' in client.service._operations  # noqa: SLF001

    stop = True
    await task

    assert max(gaps) < 0.2


@pytest.mark.parametrize('scheme', ['path', 'file'])
async def test_local_wsdl(tmp_path: Path, scheme: str):
    path = tmp_path / 'svc.wsdl'
    path.write_bytes(WSDL)
    wsdl = path.as_uri() if scheme == 'file' else str(path)

    async with soap_client(wsdl) as client:
        assert 'Echo' in client.service._operations  # noqa: SLF001


async def test_abuild_soap_client_empty_url():
    transport = AsyncTransport.from_config()

    try:
        with pytest.raises(ValueError, match='wsdl_url'):
            await abuild_soap_client('', transport)
    finally:
        await transport.aclose()


async def test_config_mapped_to_requests_session():
    config = TransportConfig(timeout=7, operation_timeout=3, verify_ssl=False, proxy='http://proxy:3128')
    transport = AsyncTransport(session=object(), config=config)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    assert transport.load_timeout == 7
    assert transport.operation_timeout == 3
    assert transport.session.verify is False
    assert transport.session.proxies == {'http': 'http://proxy:3128', 'https': 'http://proxy:3128'}


async def test_operation_timeout_honoured(server: TestServer):
    config = TransportConfig(operation_timeout=1)
    transport = AsyncTransport.from_config(config)
    transport.operation_timeout = 0.1  # type: ignore[assignment]
    url = str(server.make_url('/op'))

    try:
        with pytest.raises(TimeoutError):
            await transport.post(url, b'<x/>', {})
    finally:
        await transport.aclose()


async def test_settings_timeout_honoured(server: TestServer):
    transport = AsyncTransport.from_config()
    url = str(server.make_url('/op'))

    try:
        with transport.settings(timeout=0.1), pytest.raises(TimeoutError):
            await transport.post(url, b'<x/>', {})
    finally:
        await transport.aclose()


async def test_post_without_timeout_omits_argument(server: TestServer):
    transport = AsyncTransport.from_config()
    transport.operation_timeout = None

    try:
        resp = await transport.post(str(server.make_url('/op')), b'<x/>', {})

        assert resp.content == b'late'
    finally:
        await transport.aclose()


async def test_build_response_lowercase_and_duplicate_headers():
    class FakeResp:
        status = 200
        headers = CIMultiDictProxy(
            CIMultiDict([('content-type', 'multipart/related; type="application/xop+xml"'), ('x-a', '1'), ('X-A', '2')])
        )
        charset = None
        url = 'http://x'
        cookies: dict[str, str] = {}  # noqa: RUF012

    r = AsyncTransport._build_response(FakeResp(), b'')  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]  # noqa: SLF001

    assert r.headers.get('Content-Type') == 'multipart/related; type="application/xop+xml"'
    assert r.headers['X-A'] == '1, 2'


async def test_owned_session_closed_borrowed_not():
    owned = AsyncTransport.from_config()
    owned_session = owned._session  # noqa: SLF001
    await owned.aclose()
    assert owned_session.closed

    async with aiohttp.ClientSession() as borrowed:
        transport = AsyncTransport.from_config(session=borrowed)
        await transport.aclose()
        assert not borrowed.closed


async def test_soap_client_drains_zeep_audit_plugin(tmp_path: Path, mocker):
    wsdl = tmp_path / 'svc.wsdl'
    wsdl.write_bytes(WSDL)
    written: list[str] = []

    async def slow_put(**kwargs):
        await asyncio.sleep(0.2)
        written.append(kwargs['path'])

    mocker.patch.object(get_api_client_settings(), 'audit_path', 'file:///x')
    mocker.patch('python3_commons.object_storage.put_object', side_effect=slow_put)
    plugin = zeep_audit.ZeepAuditPlugin('svc')  # looked up late: test_audit reloads the module
    operation = mocker.MagicMock()
    operation.name = 'Echo'

    async with soap_client(str(wsdl), plugins=[plugin]):
        plugin.egress(etree.Element('Envelope'), {}, operation, {})
        plugin.ingress(etree.Element('Envelope'), {}, operation)

        assert not written

    assert len(written) == 2
    assert not plugin._tasks  # noqa: SLF001


@pytest_asyncio.fixture
async def echo_server() -> AsyncIterator[TestServer]:
    async def echo_post(request: web.Request) -> web.Response:
        response = web.Response(body=await request.read(), content_type='text/xml')
        response.set_cookie('sid', 'abc')

        return response

    async def echo_get(request: web.Request) -> web.Response:
        return web.Response(text=request.query_string)

    app = web.Application()
    app.router.add_post('/op', echo_post)
    app.router.add_get('/op', echo_get)
    srv = TestServer(app)
    await srv.start_server()

    yield srv

    await srv.close()


async def test_post_xml_serialises_envelope_and_maps_cookies(echo_server: TestServer):
    async with AsyncTransport.from_config() as transport:
        resp = await transport.post_xml(str(echo_server.make_url('/op')), etree.Element('Envelope'), {})

    assert resp.status_code == 200
    assert resp.content.endswith(b'<Envelope/>')
    assert resp.cookies.get('sid') == 'abc'
    assert resp.headers['Content-Type'].startswith('text/xml')


async def test_get_passes_params_and_honours_timeout(echo_server: TestServer, server: TestServer):
    async with AsyncTransport.from_config() as transport:
        resp = await transport.get(str(echo_server.make_url('/op')), {'a': '1'}, {})

        assert resp.content == b'a=1'

        transport.operation_timeout = 0.1  # type: ignore[assignment]

        with pytest.raises(TimeoutError):
            await transport.get(str(server.make_url('/svc')), {}, {})


async def test_get_without_timeout_omits_argument(echo_server: TestServer):
    transport = AsyncTransport.from_config()
    transport.operation_timeout = None

    try:
        resp = await transport.get(str(echo_server.make_url('/op')), {'b': '2'}, {})

        assert resp.content == b'b=2'
    finally:
        await transport.aclose()


async def test_async_context_manager_closes_owned_session():
    async with AsyncTransport.from_config() as transport:
        session = transport._session  # noqa: SLF001

        assert not session.closed

    assert session.closed


async def test_verify_ssl_false_disables_certificate_verification():
    transport = AsyncTransport.from_config(TransportConfig(verify_ssl=False))

    try:
        assert transport._session.connector._ssl is False  # type: ignore[union-attr]  # noqa: SLF001  # ty: ignore[unresolved-attribute]
    finally:
        await transport.aclose()


async def test_build_runs_in_worker_thread(tmp_path: Path, mocker: MockerFixture):
    wsdl = tmp_path / 'svc.wsdl'
    wsdl.write_bytes(WSDL)
    threads: list[int] = []
    real_build = soap_client_module.build_soap_client

    def recording_build(*args: Any, **kwargs: Any) -> Any:
        threads.append(threading.get_ident())

        return real_build(*args, **kwargs)

    mocker.patch.object(soap_client_module, 'build_soap_client', side_effect=recording_build)
    transport = AsyncTransport.from_config()

    try:
        await abuild_soap_client(str(wsdl), transport)
    finally:
        await transport.aclose()

    assert threads
    assert threads[0] != threading.get_ident()
