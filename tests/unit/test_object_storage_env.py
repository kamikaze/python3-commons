"""Pin which environment variables ``ObjectStorageClient()`` signs requests with (documented in README.md)."""

import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from os import devnull, environ
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip('object_storage_client')

_BODY = b'hello'
_SCRIPT = """
import asyncio

from object_storage_client import ObjectStorageClient


async def main() -> None:
    print((await ObjectStorageClient().get_object('s3://bucket/key')).decode())


asyncio.run(main())
"""
_KEY_ID = 'AKIATESTKEYID00000'


class _Recorder(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(('127.0.0.1', 0), _Handler)
        self.seen: list[tuple[str, str | None]] = []


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        cast('_Recorder', self.server).seen.append((self.path, self.headers.get('Authorization')))
        self.send_response(200)
        self.send_header('Content-Length', str(len(_BODY)))
        self.send_header('Last-Modified', 'Mon, 02 Nov 2026 00:00:00 GMT')
        self.end_headers()
        self.wfile.write(_BODY)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@pytest.fixture
def server():
    srv = _Recorder()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _endpoint_vars(prefix: str, endpoint: str) -> dict[str, str]:
    if prefix == 'S3':
        return {
            'S3_ACCESS_KEY_ID': _KEY_ID,
            'S3_SECRET_ACCESS_KEY': 'secret',
            'S3_REGION': 'us-east-1',
            'S3_ENDPOINT': endpoint,
            'S3_ALLOW_HTTP': 'true',
        }

    return {
        'AWS_ACCESS_KEY_ID': _KEY_ID,
        'AWS_SECRET_ACCESS_KEY': 'secret',
        'AWS_REGION': 'us-east-1',
        'AWS_ENDPOINT': endpoint,
    }


@pytest.mark.parametrize('prefix', ['S3', 'AWS'], ids=['s3-variables', 'aws-variables'])
def test_profileless_client_signs_with_environment_credentials(prefix: str, server: _Recorder, tmp_path: Path) -> None:
    env = {
        'PATH': environ.get('PATH', ''),
        'HOME': str(tmp_path),
        'AWS_CONFIG_FILE': devnull,
        'AWS_SHARED_CREDENTIALS_FILE': devnull,
        **_endpoint_vars(prefix, f'http://127.0.0.1:{server.server_port}'),
    }

    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', _SCRIPT], env=env, capture_output=True, text=True, timeout=60, check=True
    )

    assert result.stdout.strip() == _BODY.decode()
    assert len(server.seen) == 1
    path, authorization = server.seen[0]
    assert path == '/bucket/key'
    assert authorization is not None
    assert f'Credential={_KEY_ID}/' in authorization
