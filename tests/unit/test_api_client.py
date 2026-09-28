import errno
import importlib
import sys
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientResponse, ClientResponseError, ClientSession, client_exceptions

from python3_commons.api_client import request
from python3_commons.conf import ApiClientSettings, api_client_settings


def test_api_client_settings():
    settings = ApiClientSettings()
    assert settings.audit_path is None

    custom = ApiClientSettings(audit_path='custom-audit-path')
    assert custom.audit_path == 'custom-audit-path'
    assert hasattr(api_client_settings, 'audit_path')


@pytest.mark.asyncio
async def test_api_client_request_success(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='{"status": "ok"}')
    mock_response.json = AsyncMock(return_value={'status': 'ok'})

    # Mock client_method context manager
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx
    session.cookie_jar.filter_cookies.return_value = {}

    async with request(session, base_url='https://api.example.com', uri='/data', method='get') as resp:
        data = await resp.json()
        assert data == {'status': 'ok'}


@pytest.mark.asyncio
async def test_api_client_audit_logging_enabled(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='{"result": "success"}')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx
    session.cookie_jar.filter_cookies.return_value = {}

    mock_write_audit = mocker.patch('python3_commons.api_client.audit.write_audit_data', new_callable=AsyncMock)

    async with request(
        session,
        base_url='https://api.example.com',
        uri='/data',
        method='get',
        headers={'Authorization': 'Bearer token'},
        audit_name='test_service',
    ) as resp:
        assert resp == mock_response

    # Should be called twice: once for request, once for response
    assert mock_write_audit.call_count == 2

    req_call = mock_write_audit.call_args_list[0]
    assert 'test_service/data/get_' in req_call[0][1]
    assert req_call[0][1].endswith('_request.txt')
    assert b'curl' in req_call[0][2]

    resp_call = mock_write_audit.call_args_list[1]
    assert 'test_service/data/get_' in resp_call[0][1]
    assert resp_call[0][1].endswith('_response.txt')
    assert req_call[0][2] != resp_call[0][2]
    assert resp_call[0][2] == b'{"result": "success"}'


@pytest.mark.asyncio
async def test_api_client_audit_logging_with_audit_path(mocker, monkeypatch):
    monkeypatch.setattr('python3_commons.api_client.api_client_settings.audit_path', 'custom_audit_root')

    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='{"result": "success"}')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx
    session.cookie_jar.filter_cookies.return_value = {}

    mock_write_audit = mocker.patch('python3_commons.api_client.audit.write_audit_data', new_callable=AsyncMock)

    async with request(
        session,
        base_url='https://api.example.com',
        uri='/data',
        method='get',
        headers={'Authorization': 'Bearer token'},
        audit_name='test_service',
    ) as resp:
        assert resp == mock_response

    assert mock_write_audit.call_count == 2

    req_call = mock_write_audit.call_args_list[0]
    assert req_call[0][1].startswith('custom_audit_root/')
    assert not req_call[0][1].startswith('audit/')
    assert '/audit/' not in req_call[0][1]
    assert 'test_service/data/get_' in req_call[0][1]
    assert req_call[0][1].endswith('_request.txt')

    resp_call = mock_write_audit.call_args_list[1]
    assert resp_call[0][1].startswith('custom_audit_root/')
    assert not resp_call[0][1].startswith('audit/')
    assert '/audit/' not in resp_call[0][1]
    assert 'test_service/data/get_' in resp_call[0][1]
    assert resp_call[0][1].endswith('_response.txt')


@pytest.mark.asyncio
async def test_api_client_audit_logging_disabled_by_default(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='ok')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx

    mock_write_audit = mocker.patch('python3_commons.api_client.audit.write_audit_data', new_callable=AsyncMock)

    async with request(session, base_url='https://api.example.com', uri='/data', method='get') as resp:
        assert resp == mock_response

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
async def test_api_client_error_mapping(mocker, status_code, expected_exception):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = False
    mock_response.status = status_code
    mock_response.text = AsyncMock(return_value='Error detail')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx

    with pytest.raises(expected_exception):
        async with request(session, base_url='https://api.example.com', uri='/error', method='get'):
            pass


@pytest.mark.asyncio
async def test_api_client_request_post_with_json(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.CREATED
    mock_response.text = AsyncMock(return_value='{"id": 1}')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.post.return_value = mock_ctx

    async with request(
        session,
        base_url='https://api.example.com/',
        uri='/items',
        method='post',
        headers={'X-Trace-Id': '123'},
        json={'name': 'test'},
    ) as resp:
        assert resp == mock_response

    session.post.assert_called_once()
    call_kwargs = session.post.call_args[1]
    assert call_kwargs['headers'] == {'X-Trace-Id': '123', 'Content-Type': 'application/json'}
    assert b'"name": "test"' in call_kwargs['data']


@pytest.mark.asyncio
async def test_api_client_request_post_with_data(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='ok')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.post.return_value = mock_ctx

    raw_data = b'binary payload'
    async with request(
        session,
        base_url='https://api.example.com',
        uri='/upload',
        method='post',
        data=raw_data,
    ) as resp:
        assert resp == mock_response

    session.post.assert_called_once()
    call_kwargs = session.post.call_args[1]
    assert call_kwargs['data'] == raw_data


@pytest.mark.asyncio
async def test_api_client_audit_logging_post_with_json(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='{"created": true}')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.post.return_value = mock_ctx
    session.cookie_jar.filter_cookies.return_value = {}

    mock_write_audit = mocker.patch('python3_commons.api_client.audit.write_audit_data', new_callable=AsyncMock)

    async with request(
        session,
        base_url='https://api.example.com',
        uri='/data',
        method='post',
        json={'foo': 'bar'},
        audit_name='post_service',
    ) as resp:
        assert resp == mock_response

    assert mock_write_audit.call_count == 2
    req_call = mock_write_audit.call_args_list[0]
    assert 'post_service/data/post_' in req_call[0][1]
    assert req_call[0][1].endswith('_request.txt')
    assert b'curl' in req_call[0][2]
    assert b'foo' in req_call[0][2]

    resp_call = mock_write_audit.call_args_list[1]
    assert 'post_service/data/post_' in resp_call[0][1]
    assert resp_call[0][1].endswith('_response.txt')
    assert resp_call[0][2] == b'{"created": true}'


@pytest.mark.asyncio
async def test_api_client_audit_logging_get_without_headers_or_query(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='result')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx
    session.cookie_jar.filter_cookies.return_value = {}

    mock_write_audit = mocker.patch('python3_commons.api_client.audit.write_audit_data', new_callable=AsyncMock)

    async with request(
        session,
        base_url='https://api.example.com',
        uri='/data',
        method='get',
        audit_name='test_service',
    ) as resp:
        assert resp == mock_response

    # Request is not logged because GET without headers/query has curl_request = None,
    # but response is logged
    assert mock_write_audit.call_count == 1
    resp_call = mock_write_audit.call_args_list[0]
    assert resp_call[0][1].endswith('_response.txt')


@pytest.mark.asyncio
async def test_api_client_audit_logging_empty_response(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.NO_CONTENT
    mock_response.text = AsyncMock(return_value='')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx
    session.cookie_jar.filter_cookies.return_value = {}

    mock_write_audit = mocker.patch('python3_commons.api_client.audit.write_audit_data', new_callable=AsyncMock)

    async with request(
        session,
        base_url='https://api.example.com',
        uri='/data',
        method='get',
        headers={'Accept': 'application/json'},
        audit_name='empty_service',
    ) as resp:
        assert resp == mock_response

    # Only request is logged because response_text is empty
    assert mock_write_audit.call_count == 1
    assert mock_write_audit.call_args_list[0][0][1].endswith('_request.txt')


@pytest.mark.asyncio
async def test_api_client_error_unhandled_status(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = False
    mock_response.status = HTTPStatus.INTERNAL_SERVER_ERROR
    mock_response.raise_for_status.side_effect = ClientResponseError(
        request_info=MagicMock(), history=(), status=HTTPStatus.INTERNAL_SERVER_ERROR
    )

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx

    with pytest.raises(ClientResponseError):
        async with request(session, base_url='https://api.example.com', uri='/error', method='get'):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('raised_error', 'expected_exception', 'expected_msg'),
    [
        (
            client_exceptions.ClientConnectorError(MagicMock(), OSError('connection failed')),
            ConnectionRefusedError,
            'Cient connection error',
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
async def test_api_client_connection_error_mapping(mocker, raised_error, expected_exception, expected_msg):
    session = mocker.MagicMock(spec=ClientSession)
    session.get.side_effect = raised_error

    with pytest.raises(expected_exception) as exc_info:
        async with request(session, base_url='https://api.example.com', uri='/fail', method='get'):
            pass

    assert expected_msg in str(exc_info.value)


@pytest.mark.asyncio
async def test_api_client_unhandled_client_os_error(mocker):
    session = mocker.MagicMock(spec=ClientSession)
    session.get.side_effect = client_exceptions.ClientOSError(errno.EACCES, 'Permission denied')

    with pytest.raises(client_exceptions.ClientOSError):
        async with request(session, base_url='https://api.example.com', uri='/fail', method='get'):
            pass


@pytest.mark.asyncio
async def test_api_client_url_formatting_empty_uri(mocker):
    mock_response = mocker.MagicMock(spec=ClientResponse)
    mock_response.ok = True
    mock_response.status = HTTPStatus.OK
    mock_response.text = AsyncMock(return_value='ok')

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.return_value = mock_response
    mock_ctx.__aexit__.return_value = None

    session = mocker.MagicMock(spec=ClientSession)
    session.get.return_value = mock_ctx

    async with request(session, base_url='https://api.example.com/root', uri='', method='get') as resp:
        assert resp == mock_response

    session.get.assert_called_once_with('https://api.example.com/root', params=None, headers=None, timeout=None)


def test_api_client_import_error():
    with (
        patch.dict(sys.modules, {'aiohttp': None}),
        pytest.raises(RuntimeError, match=r'Install python3-commons\[api-client\] to use this feature'),
    ):
        importlib.reload(sys.modules['python3_commons.api_client'])
    importlib.reload(sys.modules['python3_commons.api_client'])
