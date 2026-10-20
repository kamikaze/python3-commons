import errno
import logging
import os
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import Enum
from http import HTTPStatus
from json import dumps
from typing import Literal
from uuid import uuid4

try:
    from aiohttp import ClientResponse, ClientSession, ClientTimeout, client_exceptions
    from aiohttp.abc import URL
except ImportError as e:
    msg = 'Install python3-commons[api-client] to use this feature'
    raise RuntimeError(msg) from e

from python3_commons import audit
from python3_commons.conf import api_client_settings, s3_settings
from python3_commons.helpers import request_to_curl
from python3_commons.serializers.json import CustomJSONEncoder

logger = logging.getLogger(__name__)


async def _write_audit(audit_name: str, uri_path: str, method: str, request_id: str, kind: str, data: str) -> None:
    now = datetime.now(tz=UTC)
    path = f'{now:%Y/%m/%d}/{audit_name}/{uri_path}/{method}_{now:%H%M%S_%f}_{request_id}_{kind}.txt'

    if api_client_settings.audit_path:
        path = f'{api_client_settings.audit_path.strip("/")}/{path}'

    await audit.write_audit_data(s3_settings, path, data.encode('utf-8'))


async def _store_response_for_audit(
    response: ClientResponse, audit_name: str, uri_path: str, method: str, request_id: str
) -> None:
    if response_text := await response.text():
        await _write_audit(audit_name, uri_path, method, request_id, 'response', response_text)


@asynccontextmanager
async def request(
    client: ClientSession,
    base_url: str,
    uri: str,
    query: Mapping | None = None,
    method: Literal['get', 'post', 'put', 'patch', 'options', 'head', 'delete'] = 'get',
    headers: Mapping | None = None,
    json: Mapping | Sequence | str | None = None,
    data: Mapping | bytes | None = None,
    timeout: ClientTimeout | Enum | None = None,
    audit_name: str | None = None,
) -> AsyncGenerator[ClientResponse]:
    request_id = str(uuid4())[-12:]
    uri_path = uri.removesuffix('/').removeprefix('/')
    url = f'{str(base_url).removesuffix("/")}{uri}' if uri else base_url

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
            await _write_audit(audit_name, uri_path, method, request_id, 'request', curl_request)

    client_method = getattr(client, method)

    logger.debug('Requesting %s %s', method, url)

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
                            response_text = await response.text()
                            msg = response_text or 'Bad request'

                            raise ValueError(msg)
                        case HTTPStatus.TOO_MANY_REQUESTS:
                            msg = 'Too many requests'

                            raise InterruptedError(msg)
                        case _:
                            response.raise_for_status()
        else:
            if json:
                data = dumps(json, cls=CustomJSONEncoder).encode('utf-8')

                headers = {**(headers or {}), 'Content-Type': 'application/json'}

            async with client_method(url, params=query, data=data, headers=headers, timeout=timeout) as response:
                if audit_name:
                    await _store_response_for_audit(response, audit_name, uri_path, method, request_id)

                yield response
    except client_exceptions.ClientConnectorError as e:
        msg = 'Client connection error'

        raise ConnectionRefusedError(msg) from e
    except client_exceptions.ClientOSError as e:
        if e.errno in {errno.EPIPE, errno.ECONNRESET}:
            raise ConnectionResetError(os.strerror(e.errno)) from e

        raise
    except client_exceptions.ServerDisconnectedError as e:
        msg = 'Server disconnected'

        raise ConnectionResetError(msg) from e
