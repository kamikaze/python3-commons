import errno
import logging
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from http import HTTPStatus
from json import dumps
from typing import Literal
from uuid import uuid4

from aiohttp import ClientResponse, ClientSession, ClientTimeout, client_exceptions
from aiohttp.abc import URL

from python3_commons import audit
from python3_commons.conf import s3_settings
from python3_commons.helpers import request_to_curl
from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)


async def _store_response_for_audit(
    response: ClientResponse, audit_name: str, uri_path: str, method: str, request_id: str
):
    response_text = await response.text()

    if response_text:
        now = datetime.now(tz=UTC)
        date_path = now.strftime('%Y/%m/%d')
        timestamp = now.strftime('%H%M%S_%f')

        await audit.write_audit_data(
            s3_settings,
            f'{date_path}/{audit_name}/{uri_path}/{method}_{timestamp}_{request_id}_response.txt',
            response_text.encode('utf-8'),
        )


@asynccontextmanager
async def request(
    client: ClientSession,
    base_url: str,
    uri: str,
    query: Mapping | None = None,
    method: Literal['get', 'post', 'put', 'patch', 'options', 'head', 'delete'] = 'get',
    headers: Mapping | None = None,
    json: Mapping | Sequence | str | None = None,
    data: bytes | None = None,
    timeout: ClientTimeout | Enum | None = None,
    audit_name: str | None = None,
) -> AsyncGenerator[ClientResponse]:
    now = datetime.now(tz=UTC)
    date_path = now.strftime('%Y/%m/%d')
    timestamp = now.strftime('%H%M%S_%f')
    request_id = str(uuid4())[-12:]
    uri_path = uri.removesuffix('/')
    uri_path = uri_path.removeprefix('/')
    url = f'{u[:-1] if (u := str(base_url)).endswith("/") else u}{uri}'

    if audit_name:
        curl_request = None
        cookies = client.cookie_jar.filter_cookies(URL(base_url)) if base_url else None

        if method == 'get':
            if headers or query:
                curl_request = request_to_curl(url=url, query=query, method=method, headers=headers, cookies=cookies)
        else:
            curl_request = request_to_curl(
                url=url, query=query, method=method, headers=headers, cookies=cookies, json=json, data=data
            )

        if curl_request:
            await audit.write_audit_data(
                s3_settings,
                f'{date_path}/{audit_name}/{uri_path}/{method}_{timestamp}_{request_id}_request.txt',
                curl_request.encode('utf-8'),
            )

    client_method = getattr(client, method)

    logger.debug(f'Requesting {method} {url}')

    try:
        if method == 'get':
            async with client_method(url, params=query, headers=headers, timeout=timeout) as response:
                if audit_name:
                    await _store_response_for_audit(response, audit_name, uri_path, method, request_id)

                if response.ok:
                    yield response
                else:
                    match response.status:
                        case HTTPStatus.UNAUTHORIZED:
                            msg = 'Unauthorized'

                            raise PermissionError(msg)
                        case HTTPStatus.FORBIDDEN:
                            msg = 'Forbidden'

                            raise PermissionError(msg)
                        case HTTPStatus.NOT_FOUND:
                            msg = 'Not found'

                            raise LookupError(msg)
                        case HTTPStatus.BAD_REQUEST:
                            msg = 'Bad request'

                            raise ValueError(msg)
                        case HTTPStatus.TOO_MANY_REQUESTS:
                            msg = 'Too many requests'

                            raise InterruptedError(msg)
                        case _:
                            response.raise_for_status()
        else:
            if json:
                data = dumps(json, cls=CustomJSONEncoder).encode('utf-8')

                if headers:
                    headers = {**headers, 'Content-Type': 'application/json'}
                else:
                    headers = {'Content-Type': 'application/json'}

            async with client_method(url, params=query, data=data, headers=headers, timeout=timeout) as response:
                if audit_name:
                    await _store_response_for_audit(response, audit_name, uri_path, method, request_id)

                yield response
    except client_exceptions.ClientConnectorError as e:
        msg = 'Cient connection error'

        raise ConnectionRefusedError(msg) from e
    except client_exceptions.ClientOSError as e:
        if e.errno == errno.EPIPE:
            msg = 'Broken pipe'

            raise ConnectionResetError(msg) from e
        elif e.errno == errno.ECONNRESET:
            msg = 'Connection reset by peer'

            raise ConnectionResetError(msg) from e

        raise
    except client_exceptions.ServerDisconnectedError as e:
        msg = 'Server disconnected'

        raise ConnectionResetError(msg) from e
