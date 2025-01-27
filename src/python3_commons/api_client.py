from contextlib import asynccontextmanager
from datetime import datetime, UTC
from typing import Literal, Mapping
from urllib.parse import urlencode

import aiohttp
from pydantic import HttpUrl

from python3_commons import audit
from python3_commons.conf import s3_settings
from python3_commons.helpers import request_to_curl


@asynccontextmanager
async def request(
        base_url: HttpUrl,
        uri: str,
        params: Mapping | None = None,
        method: Literal['get', 'post', 'patch', 'put', 'delete'] = 'get',
        audit_name: str | None = None,
):
    now = datetime.now(tz=UTC)
    date_path = now.strftime('%Y/%m/%d')
    timestamp = now.strftime('%H%M%S_%f')
    uri_path = uri[:-1] if uri.endswith('/') else uri
    uri_path = uri_path[1:] if uri_path.startswith('/') else uri_path
    url = f'{base_url}{uri}'

    if params:
        url_with_params = f'{url}?{urlencode(params)}'

        if audit_name:
            curl_request = request_to_curl(url_with_params, method, {}, None)
            await audit.write_audit_data(
                s3_settings,
                f'{date_path}/{audit_name}/{uri_path}/{method}_{timestamp}_request.txt',
                curl_request.encode('utf-8')
            )

    async with aiohttp.ClientSession() as client:
        client_method = getattr(client, method)

        async with client_method(url, params=params) as response:
            yield response
