"""HTTP status that downstream code gives to each failure of the library, pinned per builtin exception type.

Each row has two halves: the library produces the failure through its real code path (and the test asserts the
builtin exception type), then a copy of the consumer's mapping gives that exception its status. No 0.29.x change may
alter the builtin type of a mapped error (decision A2); 1.0 updates the missing-DSN row and the serializer row.

The mapping functions are copies, not imports: python3-commons tests never import fastapi-commons.
"""

import asyncio
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from http import HTTPStatus
from typing import Any

import aiohttp
import msgspec
import pytest
import pytest_asyncio
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from pydantic import HttpUrl
from pydantic_core import ValidationError

from python3_commons import cache
from python3_commons.api_client import request
from python3_commons.auth import OIDCClient
from python3_commons.conf import DBSettings, get_valkey_settings
from python3_commons.exceptions import AppError
from python3_commons.serializers.msgspec import deserialize_msgpack, serialize_msgpack

UNHANDLED = 'unhandled'


def handler_status(exc: BaseException) -> int | str:
    """Status of fastapi_commons.handlers.handle_exceptions for *exc*, or UNHANDLED when no clause catches it.

    Copy of fastapi-commons src/fastapi_commons/handlers.py:27-63 (v0.12.1). Keep the except-order in sync.
    """
    try:
        raise exc  # noqa: TRY301
    except AppError:
        return HTTPStatus.INTERNAL_SERVER_ERROR
    except PermissionError:
        return HTTPStatus.UNAUTHORIZED
    except LookupError:
        return HTTPStatus.NOT_FOUND
    except ValidationError:  # before ValueError, so a settings error is a 500
        return HTTPStatus.INTERNAL_SERVER_ERROR
    except ConnectionRefusedError:
        return HTTPStatus.SERVICE_UNAVAILABLE
    except ValueError:
        return HTTPStatus.BAD_REQUEST
    except NotImplementedError:
        return HTTPStatus.NOT_IMPLEMENTED
    except BaseException:
        return UNHANDLED


def token_verifier_status(exc: BaseException) -> int | str:
    """Status of the token verifier's provider clause for *exc*, or UNHANDLED when it does not match.

    Copy of fastapi-commons src/fastapi_commons/auth.py:75-81 (v0.12.1): `except (OSError, aiohttp.ClientError)` gives
    503. The clauses before it (JWT errors, auth.py:69-74) cannot match errors of the library.
    """
    try:
        raise exc  # noqa: TRY301
    except OSError, aiohttp.ClientError:
        return HTTPStatus.SERVICE_UNAVAILABLE
    except BaseException:
        return UNHANDLED


def _respond_with(status: int, body: str = '') -> Callable[[web.Request], Awaitable[web.Response]]:
    async def handler(_: web.Request) -> web.Response:
        return web.Response(status=status, text=body)

    return handler


@pytest_asyncio.fixture
async def make_server() -> AsyncIterator[Callable[[Callable[[web.Request], Any]], Awaitable[TestServer]]]:
    servers: list[TestServer] = []

    async def make(handler: Callable[[web.Request], Any]) -> TestServer:
        app = web.Application()
        app.router.add_get('/data', handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)

        return server

    yield make

    for server in servers:
        await server.close()


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))

        return sock.getsockname()[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('status', 'body', 'exc_type', 'expected'),
    [
        (400, 'bad input', ValueError, HTTPStatus.BAD_REQUEST),
        (401, '', PermissionError, HTTPStatus.UNAUTHORIZED),
        (403, '', PermissionError, HTTPStatus.UNAUTHORIZED),
        (404, '', LookupError, HTTPStatus.NOT_FOUND),
        (429, '', InterruptedError, UNHANDLED),
    ],
    ids=['400', '401', '403', '404', '429'],
)
async def test_api_client_status_mapping(make_server, status, body, exc_type, expected) -> None:
    server = await make_server(_respond_with(status, body))

    async with ClientSession() as session:
        with pytest.raises(exc_type) as raised:
            async with request(session, str(server.make_url('')), '/data'):
                pass

    assert type(raised.value) is exc_type
    assert handler_status(raised.value) == expected


@pytest.mark.asyncio
async def test_api_client_other_error_status_is_a_client_response_error(make_server) -> None:
    server = await make_server(_respond_with(500))

    async with ClientSession() as session:
        with pytest.raises(aiohttp.ClientResponseError) as raised:
            async with request(session, str(server.make_url('')), '/data'):
                pass

    assert handler_status(raised.value) == UNHANDLED


@pytest.mark.asyncio
async def test_api_client_connect_failure_is_connection_refused() -> None:
    async with ClientSession() as session:
        with pytest.raises(ConnectionRefusedError) as raised:
            async with request(session, f'http://127.0.0.1:{_unused_port()}', '/data'):
                pass

    assert type(raised.value) is ConnectionRefusedError
    assert handler_status(raised.value) == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_api_client_disconnect_is_connection_reset(make_server) -> None:
    async def close_transport(req: web.Request) -> web.Response:
        assert req.transport is not None
        req.transport.close()
        await asyncio.sleep(1)

        return web.Response()

    server = await make_server(close_transport)

    async with ClientSession() as session:
        with pytest.raises(ConnectionResetError) as raised:
            async with request(session, str(server.make_url('')), '/data'):
                pass

    assert type(raised.value) is ConnectionResetError
    assert handler_status(raised.value) == UNHANDLED
    assert token_verifier_status(raised.value) == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.asyncio
async def test_oidc_provider_down_is_connection_refused_and_503_in_the_token_verifier() -> None:
    async with OIDCClient(HttpUrl(f'http://127.0.0.1:{_unused_port()}'), 'client-id', timeout=5) as client:
        with pytest.raises(ConnectionRefusedError) as raised:
            await client.get_jwks()

    assert type(raised.value) is ConnectionRefusedError
    assert token_verifier_status(raised.value) == HTTPStatus.SERVICE_UNAVAILABLE


def test_missing_extra_is_a_runtime_error() -> None:
    script = """
import sys

sys.modules['aiohttp'] = None  # makes `import aiohttp` raise ImportError

try:
    import python3_commons.api_client
except BaseException as e:
    print(type(e).__name__, '|', e)
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script], capture_output=True, text=True, timeout=60, check=True
    )
    type_name, _, message = result.stdout.strip().partition(' | ')

    assert type_name == 'RuntimeError'
    assert message == 'Install python3-commons[api-client] to use this feature'
    assert handler_status(RuntimeError(message)) == UNHANDLED


def test_settings_validation_error_is_a_500_because_it_is_caught_before_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('DB_PORT', 'abc')

    with pytest.raises(ValidationError) as raised:
        DBSettings()

    assert isinstance(raised.value, ValueError)
    assert handler_status(raised.value) == HTTPStatus.INTERNAL_SERVER_ERROR


def test_unsupported_type_in_serialize_msgpack_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError) as raised:
        serialize_msgpack(object())

    assert type(raised.value) is NotImplementedError
    assert handler_status(raised.value) == HTTPStatus.NOT_IMPLEMENTED


def test_unknown_ext_code_in_deserialize_msgpack_is_not_implemented() -> None:
    data = msgspec.msgpack.encode(msgspec.msgpack.Ext(99, b'x'))

    with pytest.raises(NotImplementedError) as raised:
        deserialize_msgpack(data)

    assert type(raised.value) is NotImplementedError
    assert handler_status(raised.value) == HTTPStatus.NOT_IMPLEMENTED


@pytest.mark.asyncio
async def test_missing_valkey_dsn_is_a_value_error_and_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('VALKEY_DSN', raising=False)
    monkeypatch.delenv('VALKEY_SENTINEL_DSN', raising=False)
    get_valkey_settings.cache_clear()

    with pytest.raises(ValueError, match='VALKEY_DSN or VALKEY_SENTINEL_DSN') as raised:
        cache.get_valkey_client()

    assert type(raised.value) is ValueError
    assert handler_status(raised.value) == HTTPStatus.BAD_REQUEST
