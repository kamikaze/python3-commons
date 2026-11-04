# ruff: noqa: SLF001
import ast
import asyncio
import importlib
import logging
import subprocess
import sys
import time
import warnings
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from lxml import etree
from pydantic import ValidationError
from zeep.plugins import HistoryPlugin

from python3_commons import _paths, audit, conf, zeep_audit
from python3_commons.audit import ZeepAuditPlugin, get_history_plugin
from python3_commons.conf import (
    ApiClientSettings,
    ObjectStorageSettings,
    get_api_client_settings,
    get_object_storage_settings,
)


def test_s3_settings_no_bucket():
    with pytest.warns(DeprecationWarning, match='1.0'):
        s3_settings_cls = conf.S3Settings

    with pytest.warns(DeprecationWarning, match='1.0'):
        get_s3_settings = conf.get_s3_settings

    assert not hasattr(s3_settings_cls, 'bucket')
    assert not hasattr(s3_settings_cls, 'bucket_root')
    assert not hasattr(get_s3_settings(), 'bucket')
    assert not hasattr(get_s3_settings(), 'bucket_root')


def test_object_storage_settings_fields():
    settings = ObjectStorageSettings(work_path='s3://work-dir/')
    assert settings.work_path == 's3://work-dir'
    assert not hasattr(ObjectStorageSettings, 'audit_path')
    assert not hasattr(get_object_storage_settings(), 'audit_path')


def test_api_client_settings_fields(monkeypatch):
    settings = ApiClientSettings(audit_path='s3://audit-dir/')
    assert settings.audit_path == 's3://audit-dir'
    assert hasattr(get_api_client_settings(), 'audit_path')

    monkeypatch.setenv('API_CLIENT_AUDIT_PATH', 's3://env-audit-dir/')
    env_settings = ApiClientSettings()
    assert env_settings.audit_path == 's3://env-audit-dir'


def _configure(monkeypatch, audit_path, work_path=None):
    monkeypatch.setattr(get_object_storage_settings(), 'work_path', work_path)
    monkeypatch.setattr(get_api_client_settings(), 'audit_path', audit_path)


@pytest.mark.asyncio
async def test_write_audit_data_relative_key_under_audit_root(mocker, monkeypatch):
    _configure(monkeypatch, 's3://my-audit-root', work_path='my-work-root')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='2026/01/01/request.txt', data=b'test-data')

    assert mock_put.call_count == 1
    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 's3://my-audit-root'
    assert call_kwargs['path'] == '2026/01/01/request.txt'
    assert 'length' not in call_kwargs
    assert call_kwargs['data'].getvalue() == b'test-data'


@pytest.mark.asyncio
async def test_write_audit_data_with_full_s3_audit_path(mocker, monkeypatch):
    _configure(monkeypatch, 's3://bucket/audit')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='2026/01/01/request.txt', data=b'test-data')

    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 's3://bucket/audit'
    assert call_kwargs['path'] == '2026/01/01/request.txt'


@pytest.mark.asyncio
async def test_write_audit_data_without_audit_path_does_not_store(mocker, monkeypatch):
    _configure(monkeypatch, None, work_path='my-work-root')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='2026/01/01/request.txt', data=b'test-data')

    mock_put.assert_not_called()


@pytest.mark.asyncio
async def test_write_audit_data_empty_audit_path_does_not_store(mocker, monkeypatch):
    _configure(monkeypatch, '', work_path='my-work-root')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='2026/01/01/request.txt', data=b'test-data')

    mock_put.assert_not_called()


@pytest.mark.asyncio
async def test_write_audit_data_schemeless_audit_path_is_local_file_url(mocker, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _configure(monkeypatch, 'sl-data-importer:audit')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='2026/11/03/nvd_saml/post_x.txt', data=b'd')

    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == f'file://{tmp_path}/sl-data-importer:audit'
    assert call_kwargs['path'] == '2026/11/03/nvd_saml/post_x.txt'


@pytest.mark.asyncio
async def test_write_audit_data_no_double_slashes(mocker, monkeypatch):
    _configure(monkeypatch, 's3://bucket//audit//')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='/2026//01/nvd_saml//post_x.txt', data=b'd')

    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 's3://bucket/audit'
    assert call_kwargs['path'] == '2026/01/nvd_saml/post_x.txt'
    assert '//' not in call_kwargs['path']
    assert '//' not in call_kwargs['work_path'].removeprefix('s3://')


@pytest.mark.asyncio
async def test_write_audit_data_exception_logged_not_raised(mocker, monkeypatch):
    _configure(monkeypatch, 's3://bucket/audit')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', side_effect=RuntimeError('boom'))

    await audit.write_audit_data(key='2026/01/01/request.txt', data=b'test-data')

    assert mock_put.call_count == 1


@pytest.mark.parametrize(
    ('root', 'expected'),
    [
        ('s3://bucket/audit/', 's3://bucket/audit'),
        ('  s3://bucket//audit  ', 's3://bucket/audit'),
        ('s3://bucket', 's3://bucket'),
        ('s3://bucket:audit', 's3://bucket:audit'),
        ('S3://Bucket//Audit/', 's3://Bucket/Audit'),
        ('GCS://bucket/a', 'gcs://bucket/a'),
        ('gs://bucket/a//b', 'gs://bucket/a/b'),
        ('az://container/a', 'az://container/a'),
        ('WASB://c/a', 'wasb://c/a'),
        ('wasbs://c/a/', 'wasbs://c/a'),
        ('abfs://c/a', 'abfs://c/a'),
        ('abfss://c@acct.dfs.core.windows.net/p', 'abfss://c@acct.dfs.core.windows.net/p'),
        ('ABFSS://C@Acct.dfs.core.windows.net//p//', 'abfss://C@Acct.dfs.core.windows.net/p'),
        ('HTTPS://host/p', 'https://host/p'),
        ('http://host', 'http://host'),
        ('https://host:8443/p//q/', 'https://host:8443/p/q'),
        ('http://[::1]:9000/p', 'http://[::1]:9000/p'),
        ('file:///var/x//y/', 'file:///var/x/y'),
        ('FILE:///var/x', 'file:///var/x'),
        ('file://localhost/x', 'file://localhost/x'),
        ('file://localhost', 'file://localhost'),
        ('/var/x', 'file:///var/x'),
        ('/var//x/', 'file:///var/x'),
        ('//var/x', 'file:///var/x'),
        ('/with space/a#b/c?d', 'file:///with%20space/a%23b/c%3Fd'),
    ],
)
def test_normalize_audit_root(root, expected):
    assert audit.normalize_audit_root(root) == expected


def test_normalize_audit_root_schemeless_relative_and_home(monkeypatch, tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.chdir(cwd)

    assert audit.normalize_audit_root('~/p') == f'file://{home}/p'
    assert audit.normalize_audit_root('relative/p/') == f'file://{cwd}/relative/p'
    assert audit.normalize_audit_root('bucket') == f'file://{cwd}/bucket'
    assert audit.normalize_audit_root('./a/../b') == f'file://{cwd}/b'
    # No s3 default and no colon rewriting: the colon is kept, as in a URL path.
    assert audit.normalize_audit_root('bucket:prefix') == f'file://{cwd}/bucket:prefix'
    assert audit.normalize_audit_root('bucket:prefix/sub') == f'file://{cwd}/bucket:prefix/sub'
    assert audit.normalize_audit_root('a b#c?d') == f'file://{cwd}/a%20b%23c%3Fd'


@pytest.mark.parametrize('root', ['', '   ', '/', '//', '///', 's3://', 'file:///', 'file:////', 'S3:///'])
def test_normalize_audit_root_rejects_empty(root):
    with pytest.raises(ValueError, match='empty'):
        audit.normalize_audit_root(root)


@pytest.mark.parametrize(
    'root',
    [
        's3://bucket//audit/',
        'GCS://bucket/a',
        'https://host:8443/p//q/',
        'http://[::1]:9000/p',
        'abfss://c@acct.dfs.core.windows.net/p',
        'file://localhost/x',
        'file:///var/x//y/',
        '/var//x/',
        '/with space/a#b/c?d',
        '~/p',
        'relative/p',
        'bucket:prefix',
        'a%20b',
        'caf\u00e9/x',
    ],
)
def test_normalize_audit_root_is_idempotent(root, monkeypatch, tmp_path):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    once = audit.normalize_audit_root(root)

    assert audit.normalize_audit_root(once) == once


def test_zeep_audit_plugin_ingress_and_egress(mocker):
    plugin = ZeepAuditPlugin(audit_name='custom_plugin')
    assert plugin.audit_name == 'custom_plugin'

    mock_store = mocker.patch.object(plugin, '_store_audit')
    operation = mocker.MagicMock()
    operation.name = 'Echo'
    envelope = etree.Element('Envelope')
    headers = {'Custom': 'Header'}

    res_env, res_headers = plugin.ingress(envelope, headers, operation)
    assert res_env == envelope
    assert res_headers == headers
    mock_store.assert_called_once_with(envelope, operation, 'ingress')

    mock_store.reset_mock()
    res_env, res_headers = plugin.egress(envelope, headers, operation, binding_options={})
    assert res_env == envelope
    assert res_headers == headers
    mock_store.assert_called_once_with(envelope, operation, 'egress')


def _op(mocker, name):
    operation = mocker.MagicMock()
    operation.name = name

    return operation


@pytest.mark.asyncio
async def test_zeep_audit_plugin_store_audit_with_running_loop(mocker):
    plugin = ZeepAuditPlugin(audit_name='test_svc')
    mock_store = mocker.patch('python3_commons.audit._best_effort_store', new_callable=AsyncMock)
    envelope = etree.Element('Envelope')
    before = etree.tostring(envelope)

    plugin._store_audit(envelope, _op(mocker, 'DoStuff'), 'ingress')
    await plugin.drain()

    assert mock_store.call_count == 1
    call_args = mock_store.call_args[0]
    assert 'test_svc/DoStuff/' in call_args[0]
    assert call_args[0].endswith('_ingress.xml')
    assert b'<Envelope' in call_args[1]
    assert 'timeout' not in mock_store.call_args.kwargs
    assert etree.tostring(envelope) == before


@pytest.mark.asyncio
async def test_zeep_audit_plugin_tracks_tasks_per_plugin(mocker):
    plugin, other = ZeepAuditPlugin(audit_name='ref_svc'), ZeepAuditPlugin()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow_write(*args, **kwargs):
        started.set()
        await finish.wait()

    mocker.patch('python3_commons.audit._best_effort_store', side_effect=slow_write)
    plugin._store_audit(etree.Element('Envelope'), _op(mocker, 'SlowOp'), 'ingress')
    await started.wait()

    assert len(plugin._tasks) == 1
    assert not other._tasks
    assert len(audit._background_tasks) == 0

    finish.set()
    await plugin.drain()
    await asyncio.sleep(0)
    assert not plugin._tasks


@pytest.mark.asyncio
async def test_zeep_audit_plugin_max_pending_drops_and_logs(mocker, caplog, reset_warning_state):
    plugin = ZeepAuditPlugin(max_pending=2)
    finish = asyncio.Event()
    calls = []

    async def slow_write(path, data):
        calls.append(path)
        await finish.wait()

    mocker.patch('python3_commons.audit._best_effort_store', side_effect=slow_write)

    with caplog.at_level(logging.WARNING, logger='python3_commons.audit'):
        for _ in range(4):
            plugin._store_audit(etree.Element('Envelope'), _op(mocker, 'Op'), 'egress')

    assert len(plugin._tasks) == 2
    # Two drops within 60 s: one rate-limited WARNING, the second one is counted as suppressed
    dropped = [r for r in caplog.records if 'record dropped' in r.message]
    assert len(dropped) == 1
    assert 'backlog full' in dropped[0].message
    assert audit._suppressed == 1

    finish.set()
    await plugin.drain()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_zeep_audit_plugin_drain_waits_for_slow_put(mocker):
    plugin = ZeepAuditPlugin()
    written = []

    async def slow_put(**kwargs):
        await asyncio.sleep(0.2)
        written.append(kwargs['path'])

    mocker.patch.object(get_api_client_settings(), 'audit_path', 'file:///x')
    mocker.patch('python3_commons.object_storage.put_object', side_effect=slow_put)

    for _ in range(3):
        plugin._store_audit(etree.Element('Envelope'), _op(mocker, 'Op'), 'egress')

    assert not written
    await plugin.drain()
    assert len(written) == 3


@pytest.mark.asyncio
async def test_zeep_audit_plugin_drain_timeout_cancels_and_logs(mocker, caplog):
    plugin = ZeepAuditPlugin()
    cancelled = asyncio.Event()

    async def hang(path, data):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()

            raise

    mocker.patch('python3_commons.audit._best_effort_store', side_effect=hang)
    plugin._store_audit(etree.Element('Envelope'), _op(mocker, 'Op'), 'egress')
    await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger='python3_commons.zeep_audit'):
        await plugin.drain(timeout=0.05)

    assert cancelled.is_set()
    assert not plugin._tasks
    assert any('drain timed out' in r.message for r in caplog.records)
    assert any('write cancelled' in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_zeep_audit_plugin_drain_without_tasks_is_noop():
    await ZeepAuditPlugin().drain()


def test_zeep_audit_plugin_store_audit_without_running_loop(mocker):
    plugin = ZeepAuditPlugin(audit_name='sync_svc')
    mock_store = mocker.patch('python3_commons.audit._best_effort_store', new_callable=AsyncMock)

    plugin._store_audit(etree.Element('Envelope'), _op(mocker, 'SyncOp'), 'egress')

    assert mock_store.call_count == 1
    call_args = mock_store.call_args[0]
    assert 'sync_svc/SyncOp/' in call_args[0]
    assert call_args[0].endswith('_egress.xml')
    assert b'<Envelope' in call_args[1]


def test_zeep_audit_plugin_sync_fallback_is_bounded(mocker):
    plugin = ZeepAuditPlugin()

    async def hang(path, data):
        await asyncio.Event().wait()

    mocker.patch('python3_commons.audit._best_effort_store', side_effect=hang)
    mocker.patch.object(get_api_client_settings(), 'audit_timeout', 0.05)
    start = time.monotonic()

    plugin._store_audit(etree.Element('Envelope'), _op(mocker, 'Op'), 'egress')

    assert time.monotonic() - start < 2


@pytest.mark.asyncio
async def test_store_audit_in_s3_is_deprecated_alias(mocker):
    plugin = ZeepAuditPlugin()
    inner = mocker.patch.object(plugin, '_store_audit')
    envelope = etree.Element('Envelope')
    op = _op(mocker, 'Op')

    with pytest.warns(DeprecationWarning, match='1.0') as record:
        plugin.store_audit_in_s3(envelope, op, 'egress')

    assert record[0].filename == __file__
    inner.assert_called_once_with(envelope, op, 'egress')


@pytest.mark.asyncio
async def test_store_audit_in_s3_alias_still_stores(mocker):
    plugin = ZeepAuditPlugin()
    store = mocker.patch('python3_commons.audit._best_effort_store', new=mocker.AsyncMock())

    with pytest.warns(DeprecationWarning, match='1.0'):
        plugin.store_audit_in_s3(etree.Element('Envelope'), _op(mocker, 'Op'), 'egress')

    await plugin.drain()
    store.assert_awaited_once()


def test_get_history_plugin_accepts_sync_client(mocker):
    from zeep import Client  # noqa: PLC0415

    history = HistoryPlugin()
    client = mocker.Mock(spec=Client)
    client.plugins = [history]

    assert get_history_plugin(client) is history


def test_get_history_plugin(mocker):
    history = HistoryPlugin()
    client = mocker.MagicMock()
    client.plugins = [mocker.MagicMock(), history]

    assert get_history_plugin(client) is history

    client.plugins = [mocker.MagicMock()]
    assert get_history_plugin(client) is None


def test_zeep_audit_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, 'zeep', None)
    monkeypatch.delitem(sys.modules, 'python3_commons.zeep_audit')

    with pytest.raises(RuntimeError, match=r'Install python3-commons\[audit\] to use this feature'):
        importlib.import_module('python3_commons.zeep_audit')


def test_zeep_names_from_audit():
    # Old imports keep working
    assert audit.ZeepAuditPlugin is zeep_audit.ZeepAuditPlugin
    assert audit.get_history_plugin is zeep_audit.get_history_plugin
    with pytest.raises(AttributeError, match="has no attribute 'missing'"):
        _ = audit.missing


def _run_isolated(code: str) -> str:
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)  # noqa: S603

    return result.stdout.strip()


def test_api_client_without_zeep():
    # api_client imports audit: neither needs zeep any more (it was loaded by every service with authn)
    code = (
        'import sys\n'
        "sys.modules['zeep'] = None\n"
        "sys.modules['zeep.plugins'] = None\n"
        'import python3_commons.api_client\n'
        "print(sorted(m for m in ('zeep', 'python3_commons.zeep_audit') if sys.modules.get(m)))"
    )

    assert _run_isolated(code) == '[]'


def test_star_import_from_audit_does_not_load_zeep():
    code = (
        'import sys\n'
        'from python3_commons.audit import *\n'
        "print(sorted(m for m in ('zeep', 'lxml', 'python3_commons.zeep_audit') if m in sys.modules))"
    )

    assert _run_isolated(code) == '[]'


def test_star_import_from_zeep_audit_binds_only_the_plugins():
    code = (
        'before = set(globals())\n'
        'from python3_commons.zeep_audit import *\n'
        "print(sorted(set(globals()) - before - {'before'}))"
    )

    assert _run_isolated(code) == "['ZeepAuditPlugin', 'get_history_plugin']"


@pytest.fixture
def reset_warning_state(monkeypatch):
    monkeypatch.setattr(audit, '_last_warning', None)
    monkeypatch.setattr(audit, '_suppressed', 0)


def test_api_client_audit_limits_settings(monkeypatch):
    settings = ApiClientSettings()
    assert settings.audit_timeout == 5.0
    assert settings.audit_max_body_bytes == 1048576

    monkeypatch.setenv('API_CLIENT_AUDIT_TIMEOUT', '2.5')
    monkeypatch.setenv('API_CLIENT_AUDIT_MAX_BODY_BYTES', '10')
    env_settings = ApiClientSettings()
    assert env_settings.audit_timeout == 2.5
    assert env_settings.audit_max_body_bytes == 10

    with pytest.raises(ValueError, match='audit_timeout'):
        ApiClientSettings(audit_timeout=0)

    with pytest.raises(ValueError, match='audit_max_body_bytes'):
        ApiClientSettings(audit_max_body_bytes=-1)


def test_paths_module_is_stdlib_only():
    tree = ast.parse(Path(_paths.__file__).read_text())
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            imported.add(node.module.split('.')[0])

    assert imported <= set(sys.stdlib_module_names)


def test_normalize_audit_root_is_paths_normalize_root():
    assert audit.normalize_audit_root is _paths.normalize_root


@pytest.mark.parametrize(
    ('segment', 'expected'),
    [
        ('nvd_saml', 'nvd_saml'),
        ('a-b_c.d~e', 'a-b_c.d~e'),
        ('..', '_'),
        ('.', '_'),
        ('%2e%2e', '_'),
        ('%2E', '_'),
        ('a b', 'a%20b'),
        ('a?b#c', 'a%3Fb%23c'),
        ('a/b', 'a%2Fb'),
        ('..a', '..a'),
        ('x' * 300, 'x' * 200),
    ],
)
def test_audit_segment(segment, expected):
    assert _paths.audit_segment(segment) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['2026/../x.txt', '2026/%2e%2e/x.txt', './x.txt', '2026/%2E/x.txt'])
async def test_write_audit_data_rejects_dot_segments(mocker, monkeypatch, caplog, key):
    _configure(monkeypatch, 's3://bucket/audit')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key=key, data=b'd')

    mock_put.assert_not_called()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None


@pytest.mark.asyncio
async def test_write_audit_data_raise_errors(mocker, monkeypatch):
    _configure(monkeypatch, 's3://bucket/audit')
    mocker.patch('python3_commons.object_storage.put_object', side_effect=RuntimeError('boom'))

    with pytest.raises(RuntimeError, match='boom'):
        await audit.store_audit('a/b.txt', b'd')


@pytest.mark.asyncio
async def test_best_effort_store_passes_settings_and_key(mocker):
    mock_write = mocker.patch('python3_commons.audit.store_audit', new_callable=AsyncMock)

    await audit._best_effort_store('a/b.txt', b'data')

    assert mock_write.call_args[0] == ('a/b.txt', b'data')


@pytest.mark.asyncio
async def test_best_effort_store_is_bounded_by_explicit_timeout(mocker, reset_warning_state, caplog):
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    mocker.patch('python3_commons.audit.store_audit', side_effect=hang)
    started = time.monotonic()

    await audit._best_effort_store('a/b.txt', b'd', timeout=0.05)

    assert time.monotonic() - started < 1
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


@pytest.mark.asyncio
async def test_best_effort_store_timeout_defaults_to_setting(mocker, monkeypatch, reset_warning_state):
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    mocker.patch('python3_commons.audit.store_audit', side_effect=hang)
    monkeypatch.setattr(get_api_client_settings(), 'audit_timeout', 0.05)
    started = time.monotonic()

    await audit._best_effort_store('a/b.txt', b'd')

    assert time.monotonic() - started < 1


@pytest.mark.asyncio
async def test_best_effort_store_swallows_errors_without_traceback(mocker, monkeypatch, reset_warning_state, caplog):
    _configure(monkeypatch, 's3://bucket/audit')
    mocker.patch('python3_commons.object_storage.put_object', side_effect=RuntimeError('boom'))

    await audit._best_effort_store('a/b.txt', b'd')

    warnings = [r for r in caplog.records if r.name == 'python3_commons.audit']
    assert [r.levelno for r in warnings] == [logging.WARNING]
    assert warnings[0].exc_info is None
    assert 'boom' in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_best_effort_store_propagates_cancellation(mocker, reset_warning_state):
    started = asyncio.Event()

    async def hang(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    mocker.patch('python3_commons.audit.store_audit', side_effect=hang)
    task = asyncio.create_task(audit._best_effort_store('a/b.txt', b'd', timeout=30))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_best_effort_store_warning_is_rate_limited(mocker, reset_warning_state, caplog):
    mocker.patch('python3_commons.audit.store_audit', side_effect=RuntimeError('boom'))
    clock = mocker.patch('python3_commons.audit.time.monotonic')

    def messages():
        return [r.getMessage() for r in caplog.records if r.name == 'python3_commons.audit']

    for now in (1000.0, 1010.0, 1059.0):
        clock.return_value = now
        await audit._best_effort_store('a/b.txt', b'd')

    assert len(messages()) == 1
    assert 'suppressed' not in messages()[0]

    clock.return_value = 1061.0
    await audit._best_effort_store('a/b.txt', b'd')

    assert len(messages()) == 2
    assert '2 similar warnings suppressed' in messages()[1]

    clock.return_value = 1062.0
    await audit._best_effort_store('a/b.txt', b'd')
    clock.return_value = 1130.0
    await audit._best_effort_store('a/b.txt', b'd')

    assert '1 similar warnings suppressed' in messages()[2]


@pytest.mark.asyncio
async def test_store_audit_root_keyword_overrides_settings(mocker, monkeypatch):
    _configure(monkeypatch, 's3://settings/audit')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.store_audit('a//b.txt', b'd', root='s3://kw-bucket/pre/')

    assert mock_put.call_args[1]['work_path'] == 's3://kw-bucket/pre'
    assert mock_put.call_args[1]['path'] == 'a/b.txt'


@pytest.mark.asyncio
async def test_store_audit_only_work_path_configured_stores_nothing(mocker, monkeypatch):
    _configure(monkeypatch, None, work_path='my-work-root')
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.store_audit('a/b.txt', b'd')
    await audit.store_audit('a/b.txt', b'd', root='   ')

    mock_put.assert_not_called()


@pytest.mark.asyncio
async def test_write_audit_data_root_keyword(mocker, monkeypatch):
    _configure(monkeypatch, None)
    mock_put = mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    await audit.write_audit_data(key='a.txt', data=b'd', root='s3://bucket/audit')

    assert mock_put.call_args[1]['work_path'] == 's3://bucket/audit'


@pytest.mark.asyncio
async def test_write_audit_data_settings_argument_warns_naming_1_0(mocker, monkeypatch):
    _configure(monkeypatch, 's3://bucket/audit')
    mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    with pytest.warns(DeprecationWarning, match=r'settings.*1\.0') as record:
        await audit.write_audit_data(object(), 'a.txt', b'd')  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    assert record[0].filename == __file__


@pytest.mark.asyncio
async def test_audit_without_configuration_emits_no_deprecation_warning(mocker, monkeypatch):
    _configure(monkeypatch, None)
    mocker.patch('python3_commons.object_storage.put_object', new_callable=AsyncMock)

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        await audit.store_audit('a.txt', b'd')
        await audit.write_audit_data(key='a.txt', data=b'd')
        await audit._best_effort_store('a.txt', b'd')


def test_get_object_storage_settings_work_path_does_not_warn(monkeypatch):
    monkeypatch.setenv('OBJECT_STORAGE_WORK_PATH', 's3://work-dir')

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        assert get_object_storage_settings().work_path == 's3://work-dir'


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('s3://bucket:prefix/', 's3://bucket:prefix'),
        ('/var//x/', 'file:///var/x'),
        ('s3://bucket//audit//', 's3://bucket/audit'),
        ('', None),
        ('  ', None),
        (None, None),
    ],
)
def test_audit_path_validator_normalises_and_maps_blank_to_none(raw, expected, monkeypatch):
    assert ApiClientSettings(audit_path=raw).audit_path == expected

    if raw is not None:
        monkeypatch.setenv('API_CLIENT_AUDIT_PATH', raw)

        assert ApiClientSettings().audit_path == expected


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        ('S3://bucket//work//', 's3://bucket/work'),
        ('https://host:8443/p/', 'https://host:8443/p'),
        ('/var//x/', 'file:///var/x'),
        ('', None),
        ('  ', None),
        (None, None),
    ],
)
def test_work_path_validator_normalises_and_maps_blank_to_none(raw, expected, monkeypatch):
    assert ObjectStorageSettings(work_path=raw).work_path == expected

    if raw is not None:
        monkeypatch.setenv('OBJECT_STORAGE_WORK_PATH', raw)

        assert ObjectStorageSettings().work_path == expected


def test_work_path_validator_makes_a_relative_path_a_file_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert ObjectStorageSettings(work_path='data/x').work_path == f'file://{tmp_path}/data/x'


@pytest.mark.parametrize('raw', ['/', 's3://', 'file:///'])
@pytest.mark.parametrize('settings_cls', [ObjectStorageSettings, ApiClientSettings])
def test_root_validators_reject_an_empty_root(raw, settings_cls, monkeypatch):
    env = 'OBJECT_STORAGE_WORK_PATH' if settings_cls is ObjectStorageSettings else 'API_CLIENT_AUDIT_PATH'
    monkeypatch.setenv(env, raw)

    with pytest.raises(ValidationError, match='storage root is empty'):
        settings_cls()


def test_conf_imports_and_oidc_settings_work_without_object_storage_client():
    code = (
        "import sys; sys.modules['object_storage_client'] = None\n"
        'import python3_commons.conf as conf\n'
        "conf.OIDCSettings(); conf.ApiClientSettings(audit_path='b:p')\n"
        "assert 'python3_commons.audit' not in sys.modules"
    )
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, check=False)  # noqa: S603

    assert result.returncode == 0, result.stderr
