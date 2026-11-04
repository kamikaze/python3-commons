import asyncio
import errno
import importlib
import logging
import os
import subprocess
import sys
import textwrap
import time
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientResponseError, ClientSession, ClientTimeout, client_exceptions, web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict

from python3_commons import api_client, audit, conf
from python3_commons.api_client import _write_audit, request
from python3_commons.conf import ApiClientSettings, get_api_client_settings


def test_api_client_settings(monkeypatch):
    monkeypatch.delenv('API_CLIENT_AUDIT_PATH', raising=False)
    settings = ApiClientSettings()
    assert settings.audit_path is None

    custom = ApiClientSettings(audit_path='s3://custom-audit-path/')
    assert custom.audit_path == 's3://custom-audit-path'
    assert hasattr(get_api_client_settings(), 'audit_path')

    monkeypatch.setenv('API_CLIENT_AUDIT_PATH', 's3://env-audit-path/')
    env_settings = ApiClientSettings()
    assert env_settings.audit_path == 's3://env-audit-path'


BASE = 'https://api.example.com'


@pytest.mark.asyncio
async def test_api_client_request_success(make_response, make_session):
    response = make_response(text='{"status": "ok"}')
    response.json = AsyncMock(return_value={'status': 'ok'})
    session = make_session(response)

    async with request(session, base_url=BASE, uri='/data', method='get') as resp:
        assert await resp.json() == {'status': 'ok'}

    session.request.assert_called_once_with('GET', f'{BASE}/data', params=None, headers=CIMultiDict(), data=None)


@pytest.mark.asyncio
@pytest.mark.parametrize('audit_path', [None, 'custom_audit_root', 's3://bucket/audit'])
async def test_api_client_audit_logging_enabled(mocker, monkeypatch, make_response, make_session, audit_path):
    monkeypatch.setattr(get_api_client_settings(), 'audit_path', audit_path)
    response = make_response(body=b'{"result": "success"}')
    session = make_session(response)
    mock_write_audit = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    async with request(
        session,
        base_url=BASE,
        uri='/data',
        method='get',
        headers={'Authorization': 'Bearer token'},
        audit_name='test_service',
    ) as resp:
        assert resp == response

    # Should be called twice: once for request, once for response
    assert mock_write_audit.call_count == 2

    req_call = mock_write_audit.call_args_list[0]
    assert 'test_service/data/get_' in req_call[0][0]
    assert req_call[0][0].endswith('_request.txt')
    assert b'curl' in req_call[0][1]
    assert not req_call[0][0].startswith(('custom_audit_root', 's3://', 'audit/', '/'))

    resp_call = mock_write_audit.call_args_list[1]
    assert 'test_service/data/get_' in resp_call[0][0]
    assert resp_call[0][0].endswith('_response.txt')
    assert req_call[0][1] != resp_call[0][1]
    assert resp_call[0][1] == b'{"result": "success"}'


@pytest.mark.asyncio
async def test_api_client_audit_logging_disabled_by_default(mocker, make_response, make_session):
    session = make_session(make_response(text='ok'))
    mock_write_audit = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    async with request(session, base_url=BASE, uri='/data', method='get'):
        pass

    assert mock_write_audit.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status_code', 'expected_exception'),
    [
        (HTTPStatus.UNAUTHORIZED, PermissionError),
        (HTTPStatus.FORBIDDEN, PermissionError),
        (HTTPStatus.NOT_FOUND, LookupError),
        (HTTPStatus.BAD_REQUEST, ValueError),
        (HTTPStatus.TOO_MANY_REQUESTS, InterruptedError),
    ],
)
async def test_api_client_error_mapping(make_response, make_session, status_code, expected_exception):
    session = make_session(make_response(status_code, text='Error detail'))

    with pytest.raises(expected_exception):
        async with request(session, base_url=BASE, uri='/error', method='get'):
            pass


@pytest.mark.asyncio
async def test_api_client_request_post_with_json(make_response, make_session):
    response = make_response(HTTPStatus.CREATED, text='{"id": 1}')
    session = make_session(response)

    async with request(
        session,
        base_url=f'{BASE}/',
        uri='/items',
        method='post',
        headers={'X-Trace-Id': '123'},
        json={'name': 'test'},
    ) as resp:
        assert resp == response

    session.request.assert_called_once()
    args, call_kwargs = session.request.call_args
    assert args == ('POST', f'{BASE}/items')
    assert call_kwargs['headers'] == {'X-Trace-Id': '123', 'Content-Type': 'application/json'}
    assert b'"name": "test"' in call_kwargs['data']


@pytest.mark.asyncio
async def test_api_client_request_post_with_data(make_response, make_session):
    session = make_session(make_response(text='ok'))
    raw_data = b'binary payload'

    async with request(session, base_url=BASE, uri='/upload', method='post', data=raw_data):
        pass

    session.request.assert_called_once()
    assert session.request.call_args[1]['data'] == raw_data


@pytest.mark.asyncio
async def test_api_client_audit_logging_post_with_json(mocker, make_response, make_session):
    response = make_response(body=b'{"created": true}')
    session = make_session(response)
    mock_write_audit = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    async with request(
        session,
        base_url=BASE,
        uri='/data',
        method='post',
        json={'foo': 'bar'},
        audit_name='post_service',
    ) as resp:
        assert resp == response

    assert mock_write_audit.call_count == 2
    req_call = mock_write_audit.call_args_list[0]
    assert 'post_service/data/post_' in req_call[0][0]
    assert req_call[0][0].endswith('_request.txt')
    assert b'curl' in req_call[0][1]
    assert b'foo' in req_call[0][1]

    resp_call = mock_write_audit.call_args_list[1]
    assert 'post_service/data/post_' in resp_call[0][0]
    assert resp_call[0][0].endswith('_response.txt')
    assert resp_call[0][1] == b'{"created": true}'


@pytest.mark.asyncio
async def test_api_client_audit_logging_get_without_headers_or_query(mocker, make_response, make_session):
    session = make_session(make_response(body=b'result'))
    mock_write_audit = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    async with request(session, base_url=BASE, uri='/data', method='get', audit_name='test_service'):
        pass

    # Plain GETs are audited too: the request record and the response
    assert mock_write_audit.call_count == 2
    assert mock_write_audit.call_args_list[0][0][0].endswith('_request.txt')
    assert mock_write_audit.call_args_list[1][0][0].endswith('_response.txt')


@pytest.mark.asyncio
async def test_api_client_audit_logging_empty_response(mocker, make_response, make_session):
    session = make_session(make_response(HTTPStatus.NO_CONTENT, body=b''))
    mock_write_audit = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    async with request(
        session,
        base_url=BASE,
        uri='/data',
        method='get',
        headers={'Accept': 'application/json'},
        audit_name='empty_service',
    ):
        pass

    # Only request is logged because the response body is empty
    assert mock_write_audit.call_count == 1
    assert mock_write_audit.call_args_list[0][0][0].endswith('_request.txt')


@pytest.mark.asyncio
async def test_api_client_error_unhandled_status(make_response, make_session):
    response = make_response(HTTPStatus.INTERNAL_SERVER_ERROR)
    response.raise_for_status.side_effect = ClientResponseError(
        request_info=MagicMock(), history=(), status=HTTPStatus.INTERNAL_SERVER_ERROR
    )
    session = make_session(response)

    with pytest.raises(ClientResponseError):
        async with request(session, base_url=BASE, uri='/error', method='get'):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('raised_error', 'expected_exception', 'expected_msg'),
    [
        (
            client_exceptions.ClientConnectorError(MagicMock(), OSError('connection failed')),
            ConnectionRefusedError,
            'Client connection error',
        ),
        (
            client_exceptions.ClientOSError(errno.EPIPE, 'Broken pipe'),
            ConnectionResetError,
            'Broken pipe',
        ),
        (
            client_exceptions.ClientOSError(errno.ECONNRESET, 'Connection reset'),
            ConnectionResetError,
            'Connection reset by peer',
        ),
        (
            client_exceptions.ServerDisconnectedError(message='Server disconnected'),
            ConnectionResetError,
            'Server disconnected',
        ),
    ],
)
async def test_api_client_connection_error_mapping(make_session, raised_error, expected_exception, expected_msg):
    session = make_session()
    session.request.side_effect = raised_error

    with pytest.raises(expected_exception) as exc_info:
        async with request(session, base_url=BASE, uri='/fail', method='get'):
            pass

    assert expected_msg in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_client_unhandled_client_os_error(make_session):
    session = make_session()
    session.request.side_effect = client_exceptions.ClientOSError(errno.EACCES, 'Permission denied')

    with pytest.raises(client_exceptions.ClientOSError):
        async with request(session, base_url=BASE, uri='/fail', method='get'):
            pass


@pytest.mark.asyncio
async def test_api_client_url_formatting_empty_uri(make_response, make_session):
    response = make_response(text='ok')
    session = make_session(response)

    async with request(session, base_url=f'{BASE}/root', uri='', method='get') as resp:
        assert resp == response

    session.request.assert_called_once_with('GET', f'{BASE}/root', params=None, headers=CIMultiDict(), data=None)


def test_api_client_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, 'aiohttp', None)
    monkeypatch.delitem(sys.modules, 'python3_commons.api_client')

    with pytest.raises(RuntimeError, match=r'Install python3-commons\[api-client\] to use this feature'):
        importlib.import_module('python3_commons.api_client')


@pytest.mark.asyncio
async def test_write_audit_without_uri_path_has_no_empty_segment(mocker, monkeypatch):
    monkeypatch.setattr(get_api_client_settings(), 'audit_path', 's3://bucket/audit')
    mock_write_audit = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    await _write_audit('nvd_saml', '', 'post', 'req123', 'request', 'x')
    await _write_audit('nvd_saml', 'v1/login', 'post', 'req123', 'request', 'x')

    empty_uri_key = mock_write_audit.call_args_list[0][0][0]
    assert '//' not in empty_uri_key
    assert '/nvd_saml/post_' in empty_uri_key
    assert '/nvd_saml/v1/login/post_' in mock_write_audit.call_args_list[1][0][0]


@pytest.mark.asyncio
async def test_request_forwards_explicit_timeout(make_response, make_session):
    session = make_session(make_response())
    timeout = ClientTimeout(total=3)

    async with request(session, base_url=BASE, uri='', timeout=timeout):
        pass

    session.request.assert_called_once_with('GET', BASE, params=None, headers=CIMultiDict(), data=None, timeout=timeout)


@pytest.mark.asyncio
async def test_request_session_timeout_raises_timeout_error():
    async def slow(_request: web.Request) -> web.Response:
        await asyncio.sleep(1.5)

        return web.Response(text='late')

    app = web.Application()
    app.router.add_get('/slow', slow)

    async with TestServer(app) as server, ClientSession(timeout=ClientTimeout(total=0.3)) as session:
        started = time.monotonic()

        with pytest.raises(TimeoutError):
            async with request(session, base_url=str(server.make_url('')), uri='/slow'):
                pass

        assert time.monotonic() - started < 0.5


PNG_BYTES = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x80'


def _audit_files(root):
    return sorted(p for p in root.rglob('*') if p.is_file())


@pytest.fixture
def file_audit_root(tmp_path, monkeypatch, reset_audit_warning_state):
    root = tmp_path / 'audit'
    root.mkdir()
    monkeypatch.setattr(get_api_client_settings(), 'audit_path', f'file://{root}')

    return root


@pytest.fixture
def reset_audit_warning_state(monkeypatch):
    monkeypatch.setattr(audit, '_last_warning', None)
    monkeypatch.setattr(audit, '_suppressed', 0)


async def _fetch(app_routes, uri, audit_name='svc', **kwargs):
    app = web.Application()
    app.add_routes(app_routes)

    async with (
        TestServer(app) as server,
        ClientSession() as session,
        request(session, str(server.make_url('')), uri, audit_name=audit_name, **kwargs) as response,
    ):
        return await response.read()


@pytest.mark.asyncio
async def test_audit_binary_response_stored_as_raw_bytes(file_audit_root):
    async def handler(_request):
        return web.Response(body=PNG_BYTES, content_type='image/png')

    body = await _fetch([web.get('/img', handler)], '/img')

    assert body == PNG_BYTES
    files = _audit_files(file_audit_root)
    assert sorted(f.name.rsplit('_', 1)[1] for f in files) == ['request.txt', 'response.txt']
    response_file = next(f for f in files if f.name.endswith('_response.txt'))
    assert response_file.read_bytes() == PNG_BYTES
    assert b'curl' in next(f for f in files if f.name.endswith('_request.txt')).read_bytes()


@pytest.mark.asyncio
async def test_audit_response_capped_with_marker(file_audit_root, monkeypatch):
    monkeypatch.setattr(get_api_client_settings(), 'audit_max_body_bytes', 4)

    async def handler(_request):
        return web.Response(body=b'0123456789')

    await _fetch([web.get('/big', handler)], '/big')

    stored = next(f for f in _audit_files(file_audit_root) if f.name.endswith('_response.txt')).read_bytes()
    assert stored == b'0123\n[truncated: 4 of 10 bytes stored]'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'uri',
    [
        '/../../escape',
        '/%2e%2e/%2e%2e/escape',
        '/a/./b',
        '/q?x=../../y',
        '/frag#../../z',
        '/' + 'x' * 300,
        '/a%2F..%2Fb',
    ],
)
async def test_audit_keys_stay_under_root(file_audit_root, uri):
    async def handler(_request):
        return web.Response(body=b'ok')

    await _fetch([web.get('/{tail:.*}', handler)], uri)

    files = _audit_files(file_audit_root)
    assert len(files) == 2
    outside = [p for p in file_audit_root.parent.rglob('*') if p.is_file() and not p.is_relative_to(file_audit_root)]
    assert outside == []

    for f in files:
        relative = f.relative_to(file_audit_root).parts
        assert '..' not in relative
        assert '.' not in relative
        assert all(len(part) <= 200 for part in relative[:-1])
        assert relative[3] == 'svc'


@pytest.mark.asyncio
async def test_audit_name_with_traversal_never_writes_outside_root(file_audit_root):
    async def handler(_request):
        return web.Response(body=b'ok')

    await _fetch([web.get('/data', handler)], '/data', audit_name='../../evil')

    outside = [p for p in file_audit_root.parent.rglob('*') if p.is_file() and not p.is_relative_to(file_audit_root)]
    assert outside == []


@pytest.mark.asyncio
async def test_audit_empty_uri_has_no_empty_segment_end_to_end(file_audit_root):
    async def handler(_request):
        return web.Response(body=b'ok')

    await _fetch([web.get('/', handler)], '')

    for f in _audit_files(file_audit_root):
        relative = f.relative_to(file_audit_root).parts
        assert relative[3] == 'svc'
        assert relative[4].startswith('get_')


@pytest.mark.asyncio
async def test_hung_audit_write_does_not_stall_request(mocker, file_audit_root, caplog):
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    mocker.patch('python3_commons.object_storage.put_object', side_effect=hang)

    async def handler(_request):
        return web.Response(body=b'ok')

    started = time.monotonic()
    body = await _fetch([web.get('/data', handler)], '/data', audit_timeout=0.05)

    assert body == b'ok'
    assert time.monotonic() - started < 2
    assert len([r for r in caplog.records if r.levelno == logging.WARNING and r.name == 'python3_commons.audit']) == 1


@pytest.mark.asyncio
async def test_audit_timeout_setting_bounds_request_without_argument(mocker, file_audit_root, monkeypatch):
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    mocker.patch('python3_commons.object_storage.put_object', side_effect=hang)
    monkeypatch.setenv('API_CLIENT_AUDIT_TIMEOUT', '0.05')
    get_api_client_settings.cache_clear()

    async def handler(_request):
        return web.Response(body=b'ok')

    started = time.monotonic()
    body = await _fetch([web.get('/data', handler)], '/data')

    assert body == b'ok'
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_failing_audit_store_logs_one_warning_and_no_error(mocker, file_audit_root, caplog):
    client = mocker.MagicMock()
    client.put_object = AsyncMock(side_effect=RuntimeError('store down'))
    mocker.patch('python3_commons.object_storage.get_client', return_value=client)

    async def handler(_request):
        return web.Response(body=b'ok')

    with caplog.at_level(logging.DEBUG):
        assert await _fetch([web.get('/data', handler)], '/data') == b'ok'

    assert client.put_object.await_count >= 2  # request and response records, one rate-limited WARNING
    assert [(r.name, r.levelno) for r in caplog.records if r.levelno >= logging.WARNING] == [
        ('python3_commons.audit', logging.WARNING)
    ]


@pytest.mark.asyncio
async def test_failing_audit_write_does_not_fail_request(mocker, file_audit_root):
    mocker.patch('python3_commons.object_storage.put_object', side_effect=RuntimeError('boom'))

    async def handler(_request):
        return web.Response(body=b'ok')

    assert await _fetch([web.get('/data', handler)], '/data') == b'ok'


def test_api_client_s3_settings_still_resolves():
    with pytest.warns(DeprecationWarning, match=r'api_client\.s3_settings.*1\.0.*get_s3_settings'):
        legacy = api_client.s3_settings

    assert legacy is conf._get_s3_settings()  # noqa: SLF001


def test_api_client_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match='no_such_name'):
        api_client.no_such_name  # noqa: B018


STATUS_EXCEPTIONS = {
    400: ValueError,
    401: PermissionError,
    403: PermissionError,
    404: LookupError,
    429: InterruptedError,
    500: ClientResponseError,
}
METHODS = ['get', 'post', 'put', 'patch', 'delete']


async def _call(http_server, session, uri='/data', **kwargs):
    async with request(session, http_server.base_url, uri, **kwargs) as response:
        return response.status


@pytest.mark.asyncio
async def test_positional_args_after_uri_warn(http_server, aiohttp_session):
    with pytest.warns(DeprecationWarning, match=r'keyword-only in 1\.0') as record:
        async with request(aiohttp_session, http_server.base_url, '/data', None, 'get') as response:
            assert response.status == HTTPStatus.OK

    assert record[0].filename == __file__


@pytest.mark.asyncio
async def test_keyword_args_do_not_warn(http_server, aiohttp_session):
    # filterwarnings=error turns any warning into a failure
    async with request(aiohttp_session, http_server.base_url, '/data', method='get', query=None) as response:
        assert response.status == HTTPStatus.OK


def test_request_signature_unchanged():
    import inspect  # noqa: PLC0415

    assert list(inspect.signature(request).parameters) == [
        'client',
        'base_url',
        'uri',
        'query',
        'method',
        'headers',
        'json',
        'data',
        'timeout',
        'audit_name',
        'audit_timeout',
        'raise_for_status',
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('status', list(STATUS_EXCEPTIONS))
@pytest.mark.parametrize('method', METHODS)
async def test_status_mapping_default(http_server, aiohttp_session, method, status):
    http_server.status = status

    if method == 'get':
        with pytest.raises(STATUS_EXCEPTIONS[status]):
            await _call(http_server, aiohttp_session, method=method)
    else:
        with pytest.warns(DeprecationWarning, match=r'will raise in 1\.0; pass raise_for_status=False') as record:
            assert await _call(http_server, aiohttp_session, method=method) == status

        assert record[0].filename == __file__


@pytest.mark.asyncio
@pytest.mark.parametrize('status', list(STATUS_EXCEPTIONS))
@pytest.mark.parametrize('method', METHODS)
async def test_status_mapping_raise_for_status_true(http_server, aiohttp_session, method, status):
    http_server.status = status

    with pytest.raises(STATUS_EXCEPTIONS[status]) as raised:
        await _call(http_server, aiohttp_session, method=method, raise_for_status=True)

    assert type(raised.value) is STATUS_EXCEPTIONS[status]


@pytest.mark.asyncio
@pytest.mark.parametrize('method', METHODS)
async def test_status_mapping_raise_for_status_false_yields_response(http_server, aiohttp_session, method):
    http_server.status = 404

    assert await _call(http_server, aiohttp_session, method=method, raise_for_status=False) == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('method', METHODS)
async def test_ok_response_does_not_warn(http_server, aiohttp_session, method):
    assert await _call(http_server, aiohttp_session, method=method) == 200


@pytest.mark.asyncio
async def test_method_case_is_normalised(http_server, aiohttp_session):
    await _call(http_server, aiohttp_session, method='POST')

    assert http_server.last.method == 'POST'


@pytest.mark.asyncio
@pytest.mark.parametrize('empty', [{}, []])
async def test_empty_json_is_sent(http_server, aiohttp_session, empty):
    await _call(http_server, aiohttp_session, method='put', json=empty)

    assert http_server.last.body == (b'{}' if isinstance(empty, dict) else b'[]')
    assert http_server.last.headers['Content-Type'] == 'application/json'


@pytest.mark.asyncio
async def test_json_keeps_caller_content_type_case_insensitively(http_server, aiohttp_session):
    await _call(
        http_server, aiohttp_session, method='post', json={'a': 1}, headers={'content-type': 'application/vnd+json'}
    )

    assert http_server.last.headers['Content-Type'] == 'application/vnd+json'
    assert http_server.last.body == b'{"a": 1}'


@pytest.mark.asyncio
async def test_json_is_sent_on_get(http_server, aiohttp_session):
    await _call(http_server, aiohttp_session, method='get', json={'a': 1})

    assert http_server.last.body == b'{"a": 1}'


@pytest.mark.asyncio
@pytest.mark.parametrize('uri', ['data', '/data', '//data'])
@pytest.mark.parametrize('base_suffix', ['', '/'])
async def test_uri_without_leading_slash_works(http_server, aiohttp_session, uri, base_suffix):
    async with request(aiohttp_session, http_server.base_url.rstrip('/') + base_suffix, uri):
        pass

    assert http_server.last.path_qs.lstrip('/') == 'data'


@pytest.mark.asyncio
async def test_json_and_data_together_warns_and_json_wins(http_server, aiohttp_session):
    with pytest.warns(DeprecationWarning, match=r'json and data together raise ValueError in 1\.0') as record:
        await _call(http_server, aiohttp_session, method='post', json={'a': 1}, data=b'raw')

    assert record[0].filename == __file__
    assert http_server.last.body == b'{"a": 1}'
    assert http_server.last.headers['Content-Type'] == 'application/json'


@pytest.mark.asyncio
async def test_empty_json_and_data_together_warns_and_sends_data(http_server, aiohttp_session):
    with pytest.warns(DeprecationWarning, match='json and data together'):
        await _call(http_server, aiohttp_session, method='post', json={}, data=b'raw')

    assert http_server.last.body == b'raw'
    assert http_server.last.headers['Content-Type'] != 'application/json'


@pytest.mark.asyncio
async def test_form_data_without_json_keeps_form_content_type(http_server, aiohttp_session):
    await _call(http_server, aiohttp_session, method='post', data={'a': '1'})

    assert http_server.last.headers['Content-Type'] == 'application/x-www-form-urlencoded'
    assert http_server.last.body == b'a=1'


_NO_EXTRA_SCRIPT = textwrap.dedent(
    """
    import asyncio, logging, sys
    sys.modules['object_storage_client'] = None
    import python3_commons.api_client as api_client
    from aiohttp import ClientSession, web
    from aiohttp.test_utils import TestServer

    assert 'python3_commons.audit' not in sys.modules
    assert 'python3_commons.object_storage' not in sys.modules

    async def main():
        async def handler(_):
            return web.Response(text='ok')

        app = web.Application()
        app.router.add_get('/x', handler)
        records = []
        handler_ = logging.Handler()
        handler_.emit = records.append
        logging.getLogger('python3_commons.audit').addHandler(handler_)

        async with TestServer(app) as server, ClientSession() as session:
            async with api_client.request(session, str(server.make_url('/')), 'x', audit_name='svc') as response:
                assert await response.text() == 'ok'

        return records

    records = asyncio.run(main())
    print(len(records), [r.levelname for r in records])
    """
)


def _run_no_extra(audit_path: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != 'API_CLIENT_AUDIT_PATH'}

    if audit_path:
        env['API_CLIENT_AUDIT_PATH'] = audit_path

    return subprocess.run(  # noqa: S603
        [sys.executable, '-c', _NO_EXTRA_SCRIPT], capture_output=True, text=True, timeout=60, env=env, check=False
    )


def test_request_with_audit_name_and_no_audit_path_needs_no_object_storage_extra():
    result = _run_no_extra(None)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '0 []'


def test_request_with_audit_configured_but_extra_missing_warns_and_continues():
    result = _run_no_extra('s3://bucket/audit')

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 ['WARNING']"


def test_importing_auth_loads_no_optional_packages():
    code = (
        'import sys, python3_commons.auth; '
        "print(sorted(m for m in ('zeep','lxml','requests','object_storage_client') if m in sys.modules))"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', code], capture_output=True, text=True, timeout=60, check=True
    )

    assert result.stdout.strip() == '[]'


def test_api_client_audit_attribute_is_deprecated_but_resolves():
    with pytest.warns(DeprecationWarning, match='1.0'):
        resolved = api_client.audit

    assert resolved is audit
