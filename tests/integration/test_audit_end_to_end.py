"""Audit end to end: `api_client.request` against a real aiohttp TestServer, records in a local `file://` root.

Hermetic (no containers, no network beyond loopback). The encrypted case needs the `osc` CLI for its keyring (the
`keyring` fixture skips without it), see test_object_storage_encryption.py.
"""

import logging
import re
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from python3_commons import api_client, audit, object_storage
from tests.settings_env import hermetic_settings  # noqa: F401  # autouse fixture: no developer settings or cached client

REQUEST_MARKER = 'REQ-MARKER-7a1e'
RESPONSE_MARKER = b'RESPONSE-MARKER-9c3d'

# <YYYY>/<MM>/<DD>/<audit_name>/<uri segments>/<verb>_<HHMMSS>_<micro>_<request id>_<kind>.txt
RECORD_NAME = re.compile(r'^get_\d{6}_\d{6}_[0-9a-f-]{12}_(request|response)\.txt$')


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob('*') if path.is_file())


async def _call(uri: str = '/things/42') -> bytes:
    async def handle(_: web.Request) -> web.Response:
        return web.Response(body=RESPONSE_MARKER)

    app = web.Application()
    app.router.add_get('/{tail:.*}', handle)

    async with (
        TestServer(app) as server,
        ClientSession() as session,
        api_client.request(
            session, str(server.make_url('')), uri, headers={'X-Marker': REQUEST_MARKER}, audit_name='svc'
        ) as response,
    ):
        return await response.read()


def _assert_layout(audit_root: Path) -> dict[str, Path]:
    """Two records, `request` and `response`, under <Y>/<m>/<d>/svc/things/42/; return them by kind."""
    records = _files(audit_root)

    assert len(records) == 2

    by_kind: dict[str, Path] = {}

    for record in records:
        relative = record.relative_to(audit_root)

        assert re.fullmatch(r'\d{4}', relative.parts[0])
        assert re.fullmatch(r'\d{2}', relative.parts[1])
        assert re.fullmatch(r'\d{2}', relative.parts[2])
        assert relative.parts[3:-1] == ('svc', 'things', '42')
        assert RECORD_NAME.fullmatch(relative.name), relative.name

        by_kind[relative.name.rsplit('_', 1)[1].removesuffix('.txt')] = record

    assert set(by_kind) == {'request', 'response'}

    return by_kind


@pytest.mark.asyncio
async def test_request_and_response_records_land_under_the_audit_root(tmp_path, monkeypatch):
    monkeypatch.setenv('API_CLIENT_AUDIT_PATH', f'file://{tmp_path}/audit')

    assert await _call() == RESPONSE_MARKER

    records = _assert_layout(tmp_path / 'audit')

    assert records['response'].read_bytes() == RESPONSE_MARKER
    assert REQUEST_MARKER in records['request'].read_text()
    assert '/things/42' in records['request'].read_text()


@pytest.mark.asyncio
async def test_encrypted_records_are_not_plaintext_and_read_back_through_object_storage(tmp_path, keyring, monkeypatch):
    audit_root = f'file://{tmp_path}/audit'
    monkeypatch.setenv('API_CLIENT_AUDIT_PATH', audit_root)
    monkeypatch.setenv('OSC_KEYRING_FILE', str(keyring))

    assert await _call() == RESPONSE_MARKER

    records = _assert_layout(tmp_path / 'audit')

    for record in records.values():
        stored = record.read_bytes()

        assert stored
        assert RESPONSE_MARKER not in stored
        assert REQUEST_MARKER.encode() not in stored

    keys = {obj['Key'] async for obj in object_storage.list_objects(audit_root)}

    assert keys == {str(record.relative_to(tmp_path / 'audit')) for record in records.values()}

    key_of = {kind: str(record.relative_to(tmp_path / 'audit')) for kind, record in records.items()}

    assert await object_storage.get_object(audit_root, key_of['response']) == RESPONSE_MARKER
    assert REQUEST_MARKER.encode() in await object_storage.get_object(audit_root, key_of['request'])


@pytest.mark.asyncio
async def test_a_failing_audit_store_drops_the_records_and_not_the_request(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / 'blocker'
    blocker.write_bytes(b'a file where the audit directory should be')
    monkeypatch.setenv('API_CLIENT_AUDIT_PATH', f'file://{blocker}/audit')
    monkeypatch.setattr(audit, '_last_warning', None)  # the warning is rate limited per process
    caplog.set_level(logging.WARNING, logger='python3_commons.audit')

    assert await _call() == RESPONSE_MARKER

    assert _files(tmp_path) == [blocker]
    assert any('audit write failed, record dropped' in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_no_audit_root_means_no_records(tmp_path):
    assert await _call() == RESPONSE_MARKER

    assert _files(tmp_path) == []
