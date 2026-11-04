# pyright: strict
import errno
import importlib
import logging
import os
import warnings
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from functools import wraps
from http import HTTPStatus
from json import dumps
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from python3_commons._extras import require

require('api-client', 'aiohttp', 'multidict', 'yarl')

# PEP 810 (3.15): `import python3_commons.api_client` does not import aiohttp, multidict or yarl until a name from
# them is first used; 3.14 ignores this list and imports eagerly. Nothing at module level may use these names.
__lazy_modules__ = ['aiohttp', 'multidict', 'yarl']

from aiohttp import ClientResponse, ClientSession, ClientTimeout, client_exceptions
from multidict import CIMultiDict
from yarl import URL

from python3_commons._paths import audit_segment
from python3_commons.conf import _get_s3_settings, get_api_client_settings  # pyright: ignore[reportPrivateUsage]
from python3_commons.serializers.json import CustomJSONEncoder
from python3_commons.urls import request_to_curl

if TYPE_CHECKING:
    # `X as X` marks an explicit re-export for type checkers; at runtime __getattr__ serves `audit` (deprecated)
    from python3_commons import audit as audit  # noqa: PLC0414
    from python3_commons.conf import S3Settings

    s3_settings: S3Settings  # deprecated module attribute, served by __getattr__ below

__all__ = ['HttpVerb', 'request']

logger = logging.getLogger(__name__)

type HttpVerb = Literal['get', 'post', 'put', 'patch', 'options', 'head', 'delete']
type Headers = Mapping[str, str]
type Query = Mapping[str, Any]
type JsonBody = Mapping[str, Any] | Sequence[Any] | str
type FormBody = Mapping[str, Any] | bytes


async def _write_audit(  # noqa: PLR0913, PLR0917  # private, call sites pinned by tests
    audit_name: str,
    uri_path: str,
    method: str,
    request_id: str,
    kind: str,
    data: str | bytes,
    *,
    audit_timeout: float | None = None,
) -> None:
    now = datetime.now(tz=UTC)
    segments = [
        audit_segment(segment) for segment in (audit_name, *uri_path.split('/')) if segment
    ]  # no empty segments
    prefix = ''.join(f'{segment}/' for segment in segments)
    path = f'{now:%Y/%m/%d}/{prefix}{method}_{now:%H%M%S_%f}_{request_id}_{kind}.txt'
    payload = data.encode('utf-8') if isinstance(data, str) else data

    from python3_commons import audit  # noqa: PLC0415

    await audit._best_effort_store(path, payload, timeout=audit_timeout)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


async def _store_response_for_audit(  # noqa: PLR0913, PLR0917  # private, call sites pinned by tests
    response: ClientResponse,
    audit_name: str,
    uri_path: str,
    method: str,
    request_id: str,
    audit_timeout: float | None,
) -> None:
    if body := await response.read():
        limit = get_api_client_settings().audit_max_body_bytes

        if len(body) > limit:
            body = body[:limit] + f'\n[truncated: {limit} of {len(body)} bytes stored]'.encode()

        await _write_audit(audit_name, uri_path, method, request_id, 'response', body, audit_timeout=audit_timeout)


async def _raise_for_status(response: ClientResponse) -> None:
    """Map a non-ok response to the builtin exception types (A2)."""
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


# request() is an asynccontextmanager, so the warning is raised from the generator:
# generator -> contextlib __aenter__ -> the caller's ``async with``.
_CALLER_STACKLEVEL = 3


@asynccontextmanager
async def _request(  # noqa: C901, PLR0913, PLR0917  # public signature is pinned (C1/C2)
    client: ClientSession,
    base_url: str,
    uri: str,
    query: Query | None = None,
    method: HttpVerb = 'get',
    headers: Headers | None = None,
    json: JsonBody | None = None,
    data: FormBody | None = None,
    timeout: ClientTimeout | float | None = None,
    audit_name: str | None = None,
    audit_timeout: float | None = None,
    raise_for_status: bool | None = None,  # noqa: FBT001  # keyword-only in 1.0 (5.2)
) -> AsyncGenerator[ClientResponse]:
    """
    ``timeout=None`` uses the session default; ``ClientTimeout()`` means no limit.

    ``raise_for_status``: ``True`` maps non-ok statuses to exceptions for every method;
    ``False`` always yields the response; ``None`` (default) maps for GET only and, for other
    methods, warns on a non-ok response (it will raise in 1.0).
    """
    request_id = str(uuid4())[-12:]
    verb = method.lower()
    uri_path = urlsplit(uri).path
    url = f'{str(base_url).rstrip("/")}/{uri.lstrip("/")}' if uri else base_url

    # A non-empty json wins over data; empty json with data sends the data (today's non-GET result).
    use_json = json is not None and not (data is not None and not json)

    if json is not None and data is not None:
        warnings.warn(
            'json and data together raise ValueError in 1.0',
            DeprecationWarning,
            stacklevel=_CALLER_STACKLEVEL,
        )

    body = dumps(json, cls=CustomJSONEncoder).encode('utf-8') if use_json else data
    request_headers: CIMultiDict[str] = CIMultiDict(headers or {})

    if use_json:
        request_headers.setdefault('Content-Type', 'application/json')

    if audit_name:
        cookies = client.cookie_jar.filter_cookies(URL(base_url)) if base_url else None
        curl_request = request_to_curl(
            url=url,
            query=query,
            method=cast('HttpVerb', verb),
            headers=request_headers,
            cookies=cookies,
            json=json if use_json else None,
            data=None if use_json else data,
        )

        await _write_audit(audit_name, uri_path, verb, request_id, 'request', curl_request, audit_timeout=audit_timeout)

    # None means the session default; ClientTimeout() means no limit.
    timeout_kwargs: dict[str, Any] = {} if timeout is None else {'timeout': timeout}

    logger.debug('Requesting %s %s', verb, url)

    try:
        async with client.request(
            verb.upper(), url, params=query, headers=request_headers, data=body, **timeout_kwargs
        ) as response:
            if audit_name:
                await _store_response_for_audit(response, audit_name, uri_path, verb, request_id, audit_timeout)

            if not response.ok:
                if raise_for_status or (raise_for_status is None and verb == 'get'):
                    await _raise_for_status(response)
                elif raise_for_status is None:
                    warnings.warn(
                        'will raise in 1.0; pass raise_for_status=False',
                        DeprecationWarning,
                        stacklevel=_CALLER_STACKLEVEL,
                    )

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


_POSITIONAL_PARAMS = 3  # client, base_url, uri stay positional in 1.0 (5.2)


def _positional_guard(
    func: Callable[..., AbstractAsyncContextManager[ClientResponse]],
) -> Callable[..., AbstractAsyncContextManager[ClientResponse]]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> AbstractAsyncContextManager[ClientResponse]:
        if len(args) > _POSITIONAL_PARAMS:
            warnings.warn(
                'passing request() arguments after uri positionally is deprecated; they are keyword-only in 1.0',
                DeprecationWarning,
                stacklevel=2,
            )

        return func(*args, **kwargs)

    return wrapper


# The wrapper keeps the signature for ``inspect`` (``__wrapped__``); 5.2 removes it.
request = _positional_guard(_request)


def __getattr__(name: str) -> Any:
    """`s3_settings` and `audit` were api_client module attributes through imports: served lazily until 1.0"""
    if name == 'audit':
        warnings.warn(
            'python3_commons.api_client.audit is deprecated and will be removed in 1.0; '
            'import python3_commons.audit instead',
            DeprecationWarning,
            stacklevel=2,
        )

        return importlib.import_module('python3_commons.audit')

    if name == 's3_settings':
        warnings.warn(
            'python3_commons.api_client.s3_settings is deprecated and will be removed in 1.0; use '
            'python3_commons.conf.get_s3_settings()',
            DeprecationWarning,
            stacklevel=2,
        )

        return _get_s3_settings()

    msg = f'module {__name__!r} has no attribute {name!r}'

    raise AttributeError(msg)
