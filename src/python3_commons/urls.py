import hashlib
import shlex
from collections.abc import Mapping, Sequence
from http.cookies import BaseCookie
from json import dumps
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl

from python3_commons.serializers.json import CustomJSONEncoder

__all__ = ['replace_origin', 'request_to_curl']


def request_to_curl(  # noqa: C901, PLR0913, PLR0917  # public signature is pinned (C1/C2)
    url: str,
    query: Mapping[Any, Any] | None = None,
    method: Literal['get', 'post', 'put', 'patch', 'options', 'head', 'delete'] = 'get',
    headers: Mapping[Any, Any] | None = None,
    cookies: BaseCookie[str] | None = None,
    json: Mapping[Any, Any] | Sequence[Any] | str | None = None,
    data: Mapping[Any, Any] | bytes | None = None,
) -> str:
    if query:
        parts = urlsplit(url)
        extra = urlencode(query, doseq=True)
        url = urlunsplit(parts._replace(query=f'{parts.query}&{extra}' if parts.query else extra))

    curl_cmd: list[str] = ['curl', '-i', '-X', method.upper(), url]
    comment = ''

    if headers:
        for key, value in headers.items():
            curl_cmd += ['-H', f'{key}: {value}']

    if cookies:
        cookie_str = '; '.join(f'{k}={v.value}' for k, v in cookies.items())
        curl_cmd += ['-b', cookie_str]

    if json is not None:
        if not headers or not any(str(key).lower() == 'content-type' for key in headers):
            curl_cmd += ['-H', 'Content-Type: application/json']

        curl_cmd += ['-d', dumps(json, cls=CustomJSONEncoder)]
    elif data:
        if isinstance(data, Mapping):
            curl_cmd += ['--data-raw', urlencode(data, doseq=True)]
        elif isinstance(data, bytes):
            try:
                curl_cmd += ['--data-raw', data.decode('utf-8')]
            except UnicodeDecodeError:
                curl_cmd += ['--data-binary', '@body.bin']
                comment = f' # binary body: {len(data)} bytes, sha256={hashlib.sha256(data).hexdigest()}'

    return ' '.join(shlex.quote(part) for part in curl_cmd) + comment


def replace_origin(url: HttpUrl, host_url: HttpUrl) -> HttpUrl:
    if host_url.host is None:
        msg = 'Host URL must have a host'
        raise ValueError(msg)

    return HttpUrl.build(
        scheme=host_url.scheme,
        host=host_url.host,
        port=host_url.port,
        path=url_path.lstrip('/') if (url_path := url.path) else url_path,
        query=url.query,
        fragment=url.fragment,
    )
