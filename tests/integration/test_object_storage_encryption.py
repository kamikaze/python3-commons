"""Encryption at rest with a real keyring, made by the `osc` CLI, against a local `file://` root.

The tests are skipped (the `keyring` fixture) when `osc` is not on PATH. Install it with
`CARGO_REGISTRIES_BIXORITY_INDEX="sparse+https://codeberg.org/api/packages/bixority/cargo/"
cargo install object-storage-client --registry bixority`.
"""

import asyncio
import logging
from collections.abc import Iterator

import pytest
from object_storage_client import DecryptionError, EncryptedObjectStorageClient, Keyring, ObjectStorageClient

from python3_commons import object_storage
from tests.integration.conftest import run_osc

MARKER = b'PLAINTEXT-MARKER-4f1c'
RELOAD_WAIT = 6  # the keyring checks its file at most every 5 s


@pytest.fixture(autouse=True)
def fresh_client() -> Iterator[None]:
    object_storage.default_client.cache_clear()

    yield

    object_storage.default_client.cache_clear()


@pytest.mark.asyncio
async def test_put_object_round_trip_encrypted(tmp_path, keyring, monkeypatch):
    """The audit side of this lives in test_audit_end_to_end.py."""
    monkeypatch.setenv('OSC_KEYRING_FILE', str(keyring))
    data_root = f'file://{tmp_path}/data'

    await object_storage.put_object(data_root, 'a.txt', MARKER)

    assert await object_storage.get_object(data_root, 'a.txt') == MARKER
    assert MARKER not in (tmp_path / 'data' / 'a.txt').read_bytes()


@pytest.mark.asyncio
async def test_rotation_without_restart(tmp_path, keyring, monkeypatch):
    monkeypatch.setenv('OSC_KEYRING_FILE', str(keyring))
    data_root = f'file://{tmp_path}/data'
    old = Keyring.from_file(keyring).status()['active']

    await object_storage.put_object(data_root, 'old.txt', b'old data')

    added = tmp_path / 'keyring.new'
    run_osc('keyring', 'add', stdin=keyring, stdout=added)
    # The added key is the one that is not the old one ("newest first" is ambiguous within the same second)
    key_ids = [key['id'] for key in Keyring.from_file(added).status()['keys']]
    assert len(key_ids) == 2
    assert old in key_ids
    new = next(key_id for key_id in key_ids if key_id != old)
    added.replace(keyring)

    run_osc('keyring', 'activate', new, stdin=keyring, stdout=added)
    added.replace(keyring)
    await asyncio.sleep(RELOAD_WAIT)

    await object_storage.put_object(data_root, 'new.txt', b'new data')

    client = object_storage.get_client()
    assert isinstance(client, EncryptedObjectStorageClient)

    assert new in (await client.inspect_encryption(f'{data_root}/new.txt'))['key_names']
    assert old in (await client.inspect_encryption(f'{data_root}/old.txt'))['key_names']
    assert await object_storage.get_object(data_root, 'old.txt') == b'old data'
    assert await object_storage.get_object(data_root, 'new.txt') == b'new data'


@pytest.mark.asyncio
async def test_a_record_written_before_the_keyring_reads_through_the_fallback(tmp_path, keyring, monkeypatch, caplog):
    data_root = f'file://{tmp_path}/data'

    await ObjectStorageClient().put_object(f'{data_root}/before.txt', b'before the keyring')

    monkeypatch.setenv('OSC_KEYRING_FILE', str(keyring))

    assert await object_storage.get_object(data_root, 'before.txt') == b'before the keyring'

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]

    assert len(warnings) == 1
    assert 'before.txt' in warnings[0].getMessage()

    async with object_storage.get_object_stream(data_root, 'before.txt') as stream:
        assert await stream.read() == b'before the keyring'


@pytest.mark.asyncio
async def test_a_truncated_encrypted_file_raises_decryption_error(tmp_path, keyring, monkeypatch):
    monkeypatch.setenv('OSC_KEYRING_FILE', str(keyring))
    data_root = f'file://{tmp_path}/data'

    await object_storage.put_object(data_root, 'a.txt', b'x' * 1000)

    path = tmp_path / 'data' / 'a.txt'
    path.write_bytes(path.read_bytes()[:-10])

    with pytest.raises(DecryptionError):
        await object_storage.get_object(data_root, 'a.txt')
