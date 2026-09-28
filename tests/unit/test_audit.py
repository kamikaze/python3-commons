import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, patch

import pytest
from lxml import etree
from pydantic import SecretStr
from zeep.plugins import HistoryPlugin

from python3_commons import audit
from python3_commons.audit import ZeepAuditPlugin, get_history_plugin
from python3_commons.conf import (
    ApiClientSettings,
    ObjectStorageSettings,
    S3Settings,
    api_client_settings,
    object_storage_settings,
    s3_settings,
)


def test_s3_settings_no_bucket():
    assert not hasattr(S3Settings, 'bucket')
    assert not hasattr(S3Settings, 'bucket_root')
    assert not hasattr(s3_settings, 'bucket')
    assert not hasattr(s3_settings, 'bucket_root')


def test_object_storage_settings_fields():
    settings = ObjectStorageSettings(work_path='work-dir')
    assert settings.work_path == 'work-dir'
    assert not hasattr(ObjectStorageSettings, 'audit_path')
    assert not hasattr(object_storage_settings, 'audit_path')


def test_api_client_settings_fields():
    settings = ApiClientSettings(audit_path='audit-dir')
    assert settings.audit_path == 'audit-dir'
    assert hasattr(api_client_settings, 'audit_path')


@pytest.mark.asyncio
async def test_write_audit_data_with_work_path(mocker, monkeypatch):
    monkeypatch.setattr(object_storage_settings, 'work_path', 'my-work-root')
    monkeypatch.setattr(api_client_settings, 'audit_path', None)
    mock_put = mocker.patch('python3_commons.audit.object_storage.put_object', new_callable=AsyncMock)

    settings = S3Settings(secret_access_key=SecretStr('secret'))
    await audit.write_audit_data(settings, '2026/01/01/request.txt', b'test-data')

    assert mock_put.call_count == 1
    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 'my-work-root'
    # In audit.py, path passed to put_object starts with 'audit/'
    assert call_kwargs['path'] == 'audit/2026/01/01/request.txt'


@pytest.mark.asyncio
async def test_write_audit_data_with_audit_path(mocker, monkeypatch):
    monkeypatch.setattr(object_storage_settings, 'work_path', 'my-work-root')
    monkeypatch.setattr(api_client_settings, 'audit_path', 'my-audit-root')
    mock_put = mocker.patch('python3_commons.audit.object_storage.put_object', new_callable=AsyncMock)

    settings = S3Settings(secret_access_key=SecretStr('secret'))
    await audit.write_audit_data(settings, '2026/01/01/request.txt', b'test-data')

    assert mock_put.call_count == 1
    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 'my-audit-root'
    # Path uses audit_path directly and does NOT prepend 'audit/'
    assert not call_kwargs['path'].startswith('audit/')
    assert call_kwargs['path'] == '2026/01/01/request.txt'


@pytest.mark.asyncio
async def test_write_audit_data_key_starts_with_audit_path(mocker, monkeypatch):
    monkeypatch.setattr(object_storage_settings, 'work_path', None)
    monkeypatch.setattr(api_client_settings, 'audit_path', 'my-audit-root')
    mock_put = mocker.patch('python3_commons.audit.object_storage.put_object', new_callable=AsyncMock)

    settings = S3Settings(secret_access_key=SecretStr('secret'))
    await audit.write_audit_data(settings, 'my-audit-root/2026/01/01/request.txt', b'test-data')

    assert mock_put.call_count == 1
    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 'my-audit-root'
    assert call_kwargs['path'] == 'my-audit-root/2026/01/01/request.txt'


@pytest.mark.asyncio
async def test_write_audit_data_default_work_path_audit(mocker, monkeypatch):
    monkeypatch.setattr(object_storage_settings, 'work_path', None)
    monkeypatch.setattr(api_client_settings, 'audit_path', None)
    mock_put = mocker.patch('python3_commons.audit.object_storage.put_object', new_callable=AsyncMock)

    settings = S3Settings(secret_access_key=SecretStr('secret'))
    await audit.write_audit_data(settings, '2026/01/01/request.txt', b'test-data')

    assert mock_put.call_count == 1
    call_kwargs = mock_put.call_args[1]
    assert call_kwargs['work_path'] == 'audit'
    assert call_kwargs['path'] == '2026/01/01/request.txt'


@pytest.mark.asyncio
async def test_write_audit_data_storage_not_configured(mocker, monkeypatch):
    monkeypatch.setattr(object_storage_settings, 'work_path', None)
    monkeypatch.setattr(api_client_settings, 'audit_path', None)
    mock_put = mocker.patch('python3_commons.audit.object_storage.put_object', new_callable=AsyncMock)

    settings = S3Settings()
    await audit.write_audit_data(settings, '2026/01/01/request.txt', b'test-data')

    mock_put.assert_not_called()


@pytest.mark.asyncio
async def test_write_audit_data_exception_logged_not_raised(mocker):
    mocker.patch('python3_commons.audit.object_storage.put_object', side_effect=RuntimeError('Storage error'))
    settings = S3Settings(secret_access_key=SecretStr('secret'))

    # Should not raise exception
    await audit.write_audit_data(settings, '2026/01/01/request.txt', b'test-data')


def test_zeep_audit_plugin_ingress_and_egress(mocker):
    plugin = ZeepAuditPlugin(audit_name='custom_plugin')
    assert plugin.audit_name == 'custom_plugin'

    mock_store = mocker.patch.object(plugin, 'store_audit_in_s3')
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


@pytest.mark.asyncio
async def test_zeep_audit_plugin_store_audit_in_s3_with_running_loop(mocker):
    plugin = ZeepAuditPlugin(audit_name='test_svc')
    mock_write = mocker.patch('python3_commons.audit.write_audit_data', new_callable=AsyncMock)
    operation = mocker.MagicMock()
    operation.name = 'DoStuff'
    envelope = etree.Element('Envelope')

    plugin.store_audit_in_s3(envelope, operation, 'ingress')
    await asyncio.sleep(0)  # let scheduled task run

    assert mock_write.call_count == 1
    call_args = mock_write.call_args[0]
    assert 'test_svc/DoStuff/' in call_args[1]
    assert call_args[1].endswith('_ingress.xml')
    assert b'<Envelope' in call_args[2]


def test_zeep_audit_plugin_store_audit_in_s3_without_running_loop(mocker):
    plugin = ZeepAuditPlugin(audit_name='sync_svc')
    mock_write = mocker.patch('python3_commons.audit.write_audit_data', new_callable=AsyncMock)
    operation = mocker.MagicMock()
    operation.name = 'SyncOp'
    envelope = etree.Element('Envelope')

    plugin.store_audit_in_s3(envelope, operation, 'egress')

    assert mock_write.call_count == 1
    call_args = mock_write.call_args[0]
    assert 'sync_svc/SyncOp/' in call_args[1]
    assert call_args[1].endswith('_egress.xml')
    assert b'<Envelope' in call_args[2]


def test_zeep_audit_plugin_store_audit_in_s3_loop_not_running(mocker):
    plugin = ZeepAuditPlugin(audit_name='not_running_svc')
    mock_write = mocker.patch('python3_commons.audit.write_audit_data', new_callable=AsyncMock)
    mock_loop = mocker.MagicMock()
    mock_loop.is_running.return_value = False
    mocker.patch('asyncio.get_running_loop', return_value=mock_loop)
    operation = mocker.MagicMock()
    operation.name = 'Op'
    envelope = etree.Element('Envelope')

    plugin.store_audit_in_s3(envelope, operation, 'ingress')
    assert mock_write.call_count == 1


def test_get_history_plugin(mocker):
    history = HistoryPlugin()
    client = mocker.MagicMock()
    client.plugins = [mocker.MagicMock(), history]

    assert get_history_plugin(client) is history

    client.plugins = [mocker.MagicMock()]
    assert get_history_plugin(client) is None


def test_audit_import_error():
    with (
        patch.dict(sys.modules, {'zeep.plugins': None}),
        pytest.raises(RuntimeError, match=r'Install python3-commons\[audit\] to use this feature'),
    ):
        importlib.reload(sys.modules['python3_commons.audit'])
    importlib.reload(sys.modules['python3_commons.audit'])
