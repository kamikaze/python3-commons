"""Optional keyring encryption of object storage (`OSC_KEYRING_FILE`) and the plaintext read fallback.

Mocked: the object-storage-client stubs offer no way to create a keyring. The fakes below replace the client classes
and the `_load_keyring` seam; `tests/integration/test_object_storage_encryption.py` runs the real thing.
"""

import asyncio
import io
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic import HttpUrl

from python3_commons import api_client, audit, object_storage
from python3_commons.auth import OIDCClient
from python3_commons.conf import get_api_client_settings

pytest.importorskip('object_storage_client')

from object_storage_client import DecryptionError, EncryptionError, NotEncryptedError, UnknownKeyError

KEYRING_PATH = '/keys/ring'
AUDIT_ROOT = 's3://bucket/audit'
WORK = 's3://bucket/data'
URL = f'{WORK}/a.txt'


class FakeStream:
    def __init__(self, chunks: list[bytes], errors: dict[int, Exception] | None = None) -> None:
        self._chunks = list(chunks)
        self._errors = errors or {}
        self.reads = 0
        self.closed = False

    async def next(self) -> bytes:
        index = self.reads
        self.reads += 1

        if index in self._errors:
            raise self._errors[index]

        return self._chunks.pop(0) if self._chunks else b''

    async def aclose(self) -> None:
        self.closed = True


@dataclass
class Store:
    """What both fake clients share: the objects and a record of every call."""

    objects: dict[str, bytes] = field(default_factory=dict)
    plaintext: set[str] = field(default_factory=set)  # an encrypting client raises NotEncryptedError for these
    open_fails: bool = False  # NotEncryptedError on open instead of on the first read
    read_errors: dict[str, Exception] = field(default_factory=dict)  # raised by get_object of the encrypting client
    later_error: Exception | None = None  # raised on the second read of an encrypting stream
    load_error: Exception | None = None
    loads: list[str] = field(default_factory=list)
    profiles: list[str | None] = field(default_factory=list)  # one entry per ObjectStorageClient(...) built
    with_encryption_calls: list[dict[str, Any]] = field(default_factory=list)
    without_encryption_calls: int = 0
    plain_puts: list[tuple[str, bytes]] = field(default_factory=list)
    encrypted_puts: list[tuple[str, bytes]] = field(default_factory=list)
    streams: list[tuple[str, str, FakeStream]] = field(default_factory=list)
    metadata_calls: list[tuple[str, str]] = field(default_factory=list)

    def metadata(self, kind: str, url: str) -> dict[str, Any]:
        self.metadata_calls.append((kind, url))

        return {'last_modified': '2026-01-02T03:04:05', 'size_bytes': len(self.objects.get(url, b'')), 'e_tag': 'e'}

    def stream(self, kind: str, url: str, errors: dict[int, Exception] | None = None) -> FakeStream:
        data = self.objects[url]
        stream = FakeStream([data[:2], data[2:]] if len(data) > 2 else [data], errors)
        self.streams.append((kind, url, stream))

        return stream


class FakeListStream:
    def __init__(self, names: list[str]) -> None:
        self._batches = [names] if names else []
        self.closed = False

    def __aiter__(self) -> FakeListStream:
        return self

    async def __anext__(self) -> list[str]:
        if not self._batches:
            raise StopAsyncIteration

        return self._batches.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class FakePlainClient:
    def __init__(self, store: Store) -> None:
        self.store = store

    def with_encryption(self, keyring: object, bind_object_key: bool = True, frame_length: int = 65536) -> Any:  # noqa: FBT001, FBT002
        self.store.with_encryption_calls.append(
            {'keyring': keyring, 'bind_object_key': bind_object_key, 'frame_length': frame_length}
        )

        return FakeEncryptedClient(self.store)

    async def put_object(self, url: str, data: bytes) -> None:
        self.store.plain_puts.append((url, data))

    async def get_object(self, url: str) -> bytes:
        return self.store.objects[url]

    async def get_object_stream(self, url: str) -> FakeStream:
        return self.store.stream('plain', url)

    async def list_objects(self, url: str) -> list[str]:
        prefix = url.rstrip('/') + '/'

        return [key.removeprefix(prefix) for key in self.store.objects if key.startswith(prefix)]

    async def list_objects_stream(self, url: str) -> FakeListStream:
        return FakeListStream(await self.list_objects(url))

    async def get_object_metadata(self, url: str) -> dict[str, Any]:
        return self.store.metadata('plain', url)


class FakeEncryptedClient:
    def __init__(self, store: Store) -> None:
        self.store = store

    def without_encryption(self) -> FakePlainClient:
        self.store.without_encryption_calls += 1

        return FakePlainClient(self.store)

    async def put_object(self, url: str, data: bytes, context: object = None) -> None:
        assert context is None
        self.store.encrypted_puts.append((url, data))

    async def get_object(self, url: str) -> bytes:
        if url in self.store.read_errors:
            raise self.store.read_errors[url]

        if url in self.store.plaintext:
            msg = f'{url} is not encrypted'
            raise NotEncryptedError(msg)

        return self.store.objects[url]

    async def get_object_stream(self, url: str) -> FakeStream:
        if url in self.store.plaintext:
            if self.store.open_fails:
                msg = f'{url} is not encrypted'
                raise NotEncryptedError(msg)

            return self.store.stream('encrypted', url, {0: NotEncryptedError(f'{url} is not encrypted')})

        errors = {1: self.store.later_error} if self.store.later_error else None

        return self.store.stream('encrypted', url, errors)

    async def list_objects(self, url: str) -> list[str]:
        return await FakePlainClient(self.store).list_objects(url)

    async def list_objects_stream(self, url: str) -> FakeListStream:
        return FakeListStream(await self.list_objects(url))

    async def get_object_metadata(self, url: str) -> dict[str, Any]:
        if url in self.store.plaintext:
            msg = f'{url} is not encrypted'
            raise NotEncryptedError(msg)

        return self.store.metadata('encrypted', url)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Store:
    """Replace the client classes and the keyring seam with fakes; no keyring variable is set."""
    fake = Store()

    def load_keyring(path: str) -> str:
        fake.loads.append(path)

        if fake.load_error is not None:
            raise fake.load_error

        return 'KEYRING'

    def plain_client(profile: str | None = None) -> FakePlainClient:
        fake.profiles.append(profile)

        return FakePlainClient(fake)

    monkeypatch.setattr(object_storage, 'ObjectStorageClient', plain_client)
    monkeypatch.setattr(object_storage, 'EncryptedObjectStorageClient', FakeEncryptedClient)
    monkeypatch.setattr(object_storage, '_load_keyring', load_keyring)

    return fake


@pytest.fixture
def encrypting(store: Store, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setenv('OSC_KEYRING_FILE', KEYRING_PATH)

    return store


@pytest.fixture
def audit_root(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(get_api_client_settings(), 'audit_path', AUDIT_ROOT)
    monkeypatch.setattr(audit, '_last_warning', None)
    monkeypatch.setattr(audit, '_suppressed', 0)

    return AUDIT_ROOT


def audit_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == 'python3_commons.audit' and r.levelno == logging.WARNING]


# ---- client selection ----


@pytest.mark.parametrize('value', [None, '', '  '])
def test_unset_empty_or_blank_keyring_variable_gives_the_plain_client(store, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv('OSC_KEYRING_FILE', value)

    client = object_storage.default_client()

    assert isinstance(client, FakePlainClient)
    assert store.loads == []
    assert store.with_encryption_calls == []


def test_keyring_variable_gives_an_encrypting_client_with_the_defaults(encrypting):
    client = object_storage.default_client()

    assert isinstance(client, FakeEncryptedClient)
    assert encrypting.loads == [KEYRING_PATH]
    assert encrypting.with_encryption_calls == [{'keyring': 'KEYRING', 'bind_object_key': True, 'frame_length': 65536}]


def test_the_client_is_built_once_also_across_event_loops(encrypting):
    async def get() -> object:
        return object_storage.default_client()

    first = asyncio.run(get())
    second = asyncio.run(get())

    assert first is second is object_storage.default_client()
    assert encrypting.loads == [KEYRING_PATH]
    assert len(encrypting.with_encryption_calls) == 1


def test_only_the_keyring_file_variable_enables_encryption(store, monkeypatch):
    monkeypatch.setenv('OSC_KEYRING_COMMAND', 'cat /keys/ring')

    assert isinstance(object_storage.default_client(), FakePlainClient)
    assert store.loads == []


def test_a_failed_load_raises_with_a_note_and_is_not_cached(encrypting):
    encrypting.load_error = EncryptionError('bad keyring')

    with pytest.raises(EncryptionError, match='bad keyring') as excinfo:
        object_storage.default_client()

    assert any('OSC_KEYRING_FILE' in note and KEYRING_PATH in note for note in excinfo.value.__notes__)

    encrypting.load_error = None

    assert isinstance(object_storage.default_client(), FakeEncryptedClient)
    assert encrypting.loads == [KEYRING_PATH, KEYRING_PATH]


def test_get_client_is_an_alias_of_default_client(encrypting):
    assert object_storage.get_client() is object_storage.default_client()
    assert encrypting.loads == [KEYRING_PATH]


def test_set_default_client_swaps_and_resets_the_client(encrypting):
    fake = FakePlainClient(encrypting)
    object_storage.set_default_client(cast('Any', fake))

    assert object_storage.default_client() is object_storage.get_client() is fake
    assert encrypting.loads == []  # the keyring is not read while a client is injected

    object_storage.set_default_client(None)

    assert isinstance(object_storage.default_client(), FakeEncryptedClient)
    assert encrypting.loads == [KEYRING_PATH]


def test_object_storage_profile_builds_the_client_with_it_and_applies_the_keyring(encrypting):
    storage = object_storage.ObjectStorage(WORK, profile='p')

    assert encrypting.profiles == ['p']
    assert isinstance(storage.client, FakeEncryptedClient)
    assert encrypting.loads == [KEYRING_PATH]
    assert encrypting.with_encryption_calls[0]['keyring'] == 'KEYRING'


def test_object_storage_profile_without_keyring_is_a_plain_client(store):
    storage = object_storage.ObjectStorage(WORK, profile='p')

    assert store.profiles == ['p']
    assert isinstance(storage.client, FakePlainClient)


def test_object_storage_without_client_or_profile_uses_the_default_client(encrypting):
    assert object_storage.ObjectStorage(WORK).client is object_storage.default_client()
    assert encrypting.profiles == [None]  # built once, by default_client()


def test_object_storage_with_a_client_builds_nothing(encrypting):
    fake = FakePlainClient(encrypting)

    assert object_storage.ObjectStorage(WORK, client=cast('Any', fake)).client is fake
    assert encrypting.profiles == []
    assert encrypting.loads == []

    with pytest.raises(ValueError, match='client or profile'):
        object_storage.ObjectStorage(WORK, client=cast('Any', fake), profile='p')


def test_importing_the_modules_builds_no_client_and_loads_no_keyring():
    script = (
        'import python3_commons.object_storage as o, python3_commons.audit, python3_commons.api_client\n'
        'assert o.default_client.cache_info().currsize == 0\n'
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-X', 'dev', '-W', 'error', '-c', script],
        env={'OSC_KEYRING_FILE': '/nonexistent', 'PATH': ''},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
    assert result.stderr == ''


# ---- writes ----


@pytest.mark.asyncio
async def test_put_object_writes_through_the_encrypting_client_unchanged(encrypting):
    url = await object_storage.put_object(WORK, 'a.txt', io.BytesIO(b'payload'))

    assert url == URL
    assert encrypting.encrypted_puts == [(URL, b'payload')]
    assert encrypting.plain_puts == []


@pytest.mark.asyncio
async def test_put_object_raises_when_the_keyring_cannot_be_loaded(encrypting):
    encrypting.load_error = EncryptionError('bad keyring')

    with pytest.raises(EncryptionError, match='bad keyring'):
        await object_storage.put_object(WORK, 'a.txt', b'payload')

    assert encrypting.plain_puts == encrypting.encrypted_puts == []


@pytest.mark.asyncio
async def test_api_client_audit_records_are_encrypted(encrypting, audit_root, http_server, aiohttp_session):
    async with api_client.request(aiohttp_session, http_server.base_url, '/data', audit_name='svc'):
        pass

    keys = [url for url, _ in encrypting.encrypted_puts]

    assert len(keys) == 2
    assert keys[0].endswith('_request.txt')
    assert keys[1].endswith('_response.txt')
    assert all(key.startswith(f'{AUDIT_ROOT}/') for key in keys)
    assert encrypting.plain_puts == []


@pytest.mark.asyncio
async def test_zeep_audit_records_are_encrypted(encrypting, audit_root, mocker):
    pytest.importorskip('zeep')
    from lxml import etree  # noqa: PLC0415

    from python3_commons.zeep_audit import ZeepAuditPlugin  # noqa: PLC0415

    plugin = ZeepAuditPlugin(audit_name='soap')
    operation = mocker.MagicMock()
    operation.name = 'Op'

    plugin.ingress(etree.Element('Envelope'), {}, operation)  # the zeep hook (store_audit_in_s3 is deprecated, 2.M)
    await plugin.drain()

    assert encrypting.encrypted_puts[0][0].endswith('_ingress.xml')
    assert b'<Envelope' in encrypting.encrypted_puts[0][1]
    assert encrypting.plain_puts == []


@pytest_asyncio.fixture
async def token_server() -> Any:
    base: dict[str, str] = {}

    async def config(_: web.Request) -> web.Response:
        return web.json_response({'jwks_uri': f'{base["url"]}/keys', 'token_endpoint': f'{base["url"]}/token'})

    async def token(_: web.Request) -> web.Response:
        return web.json_response({'access_token': 'AT', 'id_token': 'IDT', 'expires_in': 60, 'token_type': 'Bearer'})

    app = web.Application()
    app.router.add_get('/.well-known/openid-configuration', config)
    app.router.add_post('/token', token)

    async with TestServer(app) as server:
        base['url'] = str(server.make_url('')).rstrip('/')

        yield base['url']


@pytest.mark.asyncio
async def test_audit_keeps_the_raw_secrets_of_a_token_post(encrypting, audit_root, token_server):
    """S1: audit data stays raw; encryption at rest is the protection."""
    async with OIDCClient(HttpUrl(token_server), 'cid', 'CLIENT-SECRET', audit_name='oidc') as client:
        await client.fetch_token(username='alice', password='Pa55word')

    blob = b'\n'.join(data for _, data in encrypting.encrypted_puts).decode()

    assert 'Pa55word' in blob
    assert 'CLIENT-SECRET' in blob
    assert encrypting.plain_puts == []


@pytest.mark.asyncio
async def test_audit_keeps_the_raw_authorization_header(encrypting, audit_root, http_server, aiohttp_session):
    async with api_client.request(
        aiohttp_session, http_server.base_url, '/data', headers={'Authorization': 'Bearer RAW-TOKEN'}, audit_name='svc'
    ):
        pass

    assert b'Bearer RAW-TOKEN' in encrypting.encrypted_puts[0][1]


@pytest.mark.asyncio
async def test_audited_request_survives_an_unloadable_keyring_and_writes_nothing_plain(
    encrypting, audit_root, http_server, aiohttp_session, caplog
):
    encrypting.load_error = EncryptionError('bad keyring')

    for _ in range(3):
        async with api_client.request(aiohttp_session, http_server.base_url, '/data', audit_name='svc') as response:
            assert response.status == 200

    assert encrypting.plain_puts == encrypting.encrypted_puts == []
    assert len(audit_warnings(caplog)) == 1  # 6 dropped records, one rate-limited WARNING
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    encrypting.load_error = None

    async with api_client.request(aiohttp_session, http_server.base_url, '/data', audit_name='svc'):
        pass

    assert len(encrypting.encrypted_puts) == 2
    assert encrypting.plain_puts == []


# ---- reads (F10) ----


@pytest.mark.asyncio
async def test_get_object_decrypts_through_the_encrypting_client(encrypting, caplog):
    encrypting.objects[URL] = b'secret'

    assert await object_storage.get_object(WORK, 'a.txt') == b'secret'
    assert encrypting.without_encryption_calls == 0
    assert not caplog.records


@pytest.mark.asyncio
async def test_get_object_reads_a_plaintext_object_through_the_plain_view(encrypting, caplog):
    encrypting.objects[URL] = b'plain'
    encrypting.plaintext.add(URL)
    caplog.set_level(logging.DEBUG)

    assert await object_storage.get_object(WORK, 'a.txt') == b'plain'

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]

    assert [r.levelno for r in warnings] == [logging.WARNING]
    assert URL in warnings[0].getMessage()
    assert warnings[0].exc_info is None


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [UnknownKeyError('unknown key'), DecryptionError('tampered')])
async def test_get_object_does_not_retry_decryption_errors(encrypting, error):
    encrypting.objects[URL] = b'x'
    encrypting.read_errors[URL] = error

    with pytest.raises(type(error)):
        await object_storage.get_object(WORK, 'a.txt')

    assert encrypting.without_encryption_calls == 0


@pytest.mark.asyncio
async def test_get_object_failure_of_the_plain_retry_is_raised_with_a_note_and_not_logged(encrypting, caplog):
    encrypting.plaintext.add(URL)  # not in objects: the plain view raises KeyError

    with pytest.raises(KeyError) as excinfo:
        await object_storage.get_object(WORK, 'a.txt')

    assert excinfo.value.__notes__ == [f'object storage url: {URL}']
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
@pytest.mark.parametrize('open_fails', [True, False], ids=['on-open', 'on-first-read'])
async def test_get_object_stream_falls_back_to_the_plain_stream(encrypting, caplog, open_fails):
    encrypting.objects[URL] = b'plain text'
    encrypting.plaintext.add(URL)
    encrypting.open_fails = open_fails

    async with object_storage.get_object_stream(WORK, 'a.txt') as stream:
        assert await stream.read() == b'plain text'

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]

    assert [r.levelno for r in warnings] == [logging.WARNING]
    assert URL in warnings[0].getMessage()
    assert all(stream.closed for _, _, stream in encrypting.streams)
    assert [kind for kind, _, _ in encrypting.streams] == (['plain'] if open_fails else ['encrypted', 'plain'])


@pytest.mark.asyncio
async def test_get_object_stream_first_read_fallback_returns_the_plain_first_chunk(encrypting):
    encrypting.objects[URL] = b'plain text'
    encrypting.plaintext.add(URL)

    async with object_storage.get_object_stream(WORK, 'a.txt') as stream:
        assert await stream.next() == b'pl'
        assert await stream.next() == b'ain text'
        assert await stream.next() == b''


@pytest.mark.asyncio
async def test_get_object_stream_only_the_first_read_falls_back(encrypting):
    encrypting.objects[URL] = b'secret data'
    encrypting.later_error = NotEncryptedError('damaged')

    async with object_storage.get_object_stream(WORK, 'a.txt') as stream:
        assert await stream.next() == b'se'

        with pytest.raises(NotEncryptedError):
            await stream.next()

    assert encrypting.without_encryption_calls == 1  # the eager plain view; no plain stream was opened
    assert [kind for kind, _, _ in encrypting.streams] == ['encrypted']


@pytest.mark.asyncio
async def test_get_object_stream_closes_the_current_stream_on_exit(encrypting):
    encrypting.objects[URL] = b'plain text'
    encrypting.plaintext.add(URL)

    async with object_storage.get_object_stream(WORK, 'a.txt') as stream:
        await stream.next()

        assert not encrypting.streams[-1][2].closed

    assert encrypting.streams[-1][0] == 'plain'
    assert encrypting.streams[-1][2].closed


@pytest.mark.asyncio
async def test_get_objects_reads_a_plaintext_object_through_the_fallback(encrypting):
    encrypting.objects[f'{WORK}/a.txt'] = b'plain a'
    encrypting.objects[f'{WORK}/b.txt'] = b'secret b'
    encrypting.plaintext.add(f'{WORK}/a.txt')

    result = {name: data async for name, _, data in object_storage.get_objects(WORK)}

    assert result == {'a.txt': b'plain a', 'b.txt': b'secret b'}


@pytest.mark.asyncio
async def test_a_plain_client_never_asks_for_the_plain_view(store, tmp_path):
    store.objects[URL] = b'data'

    assert await object_storage.get_object(WORK, 'a.txt') == b'data'

    async with object_storage.get_object_stream(WORK, 'a.txt') as stream:
        assert await stream.read() == b'data'

    assert [obj['Key'] async for obj in object_storage.list_objects(WORK)] == ['a.txt']
    assert store.without_encryption_calls == 0
    assert store.metadata_calls == [('plain', URL)]


@pytest.mark.asyncio
async def test_list_objects_takes_metadata_from_the_plain_view(encrypting):
    encrypting.objects[f'{WORK}/a.txt'] = b'plain a'
    encrypting.objects[f'{WORK}/b.txt'] = b'secret b'
    encrypting.plaintext.add(f'{WORK}/a.txt')

    listing = {obj['Key']: obj async for obj in object_storage.list_objects(WORK)}

    assert set(listing) == {'a.txt', 'b.txt'}
    assert listing['a.txt']['Size'] == len(b'plain a')  # the real size, not the invented-metadata fallback
    assert {kind for kind, _ in encrypting.metadata_calls} == {'plain'}


# ---- injection (2.F4) ----


@pytest.mark.asyncio
async def test_set_default_client_is_used_by_module_functions_and_audit_writes(store):
    fake = FakePlainClient(store)
    object_storage.set_default_client(cast('Any', fake))

    await object_storage.put_object(WORK, 'a.txt', b'payload')
    await audit.store_audit('x/y.txt', b'audit', root=AUDIT_ROOT)

    assert store.plain_puts == [(URL, b'payload'), (f'{AUDIT_ROOT}/x/y.txt', b'audit')]
    assert store.loads == []


@pytest.mark.asyncio
async def test_object_storage_writes_and_reads_through_the_injected_client(store):
    storage = object_storage.ObjectStorage('S3://bucket//data/', client=cast('Any', FakeEncryptedClient(store)))

    assert storage.root == WORK
    assert await storage.put_object('a.txt', io.BytesIO(b'payload')) == URL
    assert store.encrypted_puts == [(URL, b'payload')]

    store.objects[URL] = b'payload'

    assert await storage.get_object('a.txt') == b'payload'

    async with storage.get_object_stream('a.txt') as stream:
        assert await stream.read() == b'payload'


@pytest.mark.asyncio
async def test_object_storage_keeps_the_plaintext_fallback_and_lists_through_the_plain_view(store, caplog):
    storage = object_storage.ObjectStorage(WORK, client=cast('Any', FakeEncryptedClient(store)))
    store.objects[URL] = b'plain'
    store.plaintext.add(URL)
    caplog.set_level(logging.DEBUG)

    assert await storage.get_object('a.txt') == b'plain'

    async with storage.get_object_stream('a.txt') as stream:
        assert await stream.read() == b'plain'

    listing = {obj['Key']: obj async for obj in storage.list_objects()}
    objects = [item async for item in storage.get_objects()]

    assert listing['a.txt']['Size'] == len(b'plain')
    assert [(key, data) for key, _, data in objects] == [('a.txt', b'plain')]
    assert {r.levelno for r in caplog.records if r.levelno >= logging.WARNING} == {logging.WARNING}
