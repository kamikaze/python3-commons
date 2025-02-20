from contextlib import asynccontextmanager
from datetime import datetime, UTC
from json import dumps
from typing import AsyncGenerator, Literal, Mapping, Sequence

from aiohttp import ClientSession, client_exceptions
from aiohttp.web_response import Response
from pydantic import HttpUrl

from python3_commons import audit
from python3_commons.conf import s3_settings
from python3_commons.helpers import request_to_curl
from python3_commons.serializers.json import CustomJSONEncoder


@asynccontextmanager
async def request(
    client: ClientSession,
    base_url: HttpUrl,
    uri: str,
    query: Mapping | None = None,
    method: Literal['get', 'post', 'put', 'patch', 'options', 'head', 'delete'] = 'get',
    headers: Mapping | None = None,
    json: Mapping | Sequence | str | None = None,
    data: bytes | None = None,
    audit_name: str | None = None
) -> AsyncGenerator[Response]:
    now = datetime.now(tz=UTC)
    date_path = now.strftime('%Y/%m/%d')
    timestamp = now.strftime('%H%M%S_%f')
    uri_path = uri[:-1] if uri.endswith('/') else uri
    uri_path = uri_path[1:] if uri_path.startswith('/') else uri_path
    url = f'{base_url}{uri}'

    if audit_name:
        curl_request = None

        if method == 'get':
            if query:
                curl_request = request_to_curl(url, query, method, headers)
        else:
            curl_request = request_to_curl(url, query, method, headers, json, data)

        if curl_request:
            await audit.write_audit_data(
                s3_settings,
                f'{date_path}/{audit_name}/{uri_path}/{method}_{timestamp}_request.txt',
                curl_request.encode('utf-8')
            )
    client_method = getattr(client, method)

    try:
        if method == 'get':
            async with client_method(url, params=query) as response:
                yield response
        else:
            if json:
                data = dumps(json, cls=CustomJSONEncoder).encode('utf-8')

                if headers:
                    headers = {**headers, 'Content-Type': 'application/json'}
                else:
                    headers = {'Content-Type': 'application/json'}

            async with client_method(url, params=query, data=data, headers=headers) as response:
                yield response
    except client_exceptions.ClientOSError as e:
        if e.errno == 32:
            raise ConnectionResetError('Broken pipe') from e
        elif e.errno == 104:
            raise ConnectionResetError('Connection reset by peer') from e

        raise
    except client_exceptions.ServerDisconnectedError as e:
        raise ConnectionResetError('Server disconnected') from e
