import subprocess
import sys
import warnings

import pytest

from python3_commons import conf
from python3_commons.conf import (
    ApiClientSettings,
    CommonSettings,
    DBSettings,
    ObjectStorageSettings,
    OIDCSettings,
    ValkeySettings,
)
from tests import settings_env

GETTERS = {
    'get_common_settings': CommonSettings,
    'get_db_settings': DBSettings,
    'get_valkey_settings': ValkeySettings,
    '_get_s3_settings': conf._S3Settings,  # noqa: SLF001
    'get_api_client_settings': ApiClientSettings,
    'get_object_storage_settings': ObjectStorageSettings,
    'get_oidc_settings': OIDCSettings,
}
LEGACY = {
    'settings': 'get_common_settings',
    'db_settings': 'get_db_settings',
    'valkey_settings': 'get_valkey_settings',
    's3_settings': 'get_s3_settings',
    'api_client_settings': 'get_api_client_settings',
    'object_storage_settings': 'get_object_storage_settings',
}


def _run(code: str, env: dict[str, str] | None = None, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, *flags, '-c', code], capture_output=True, text=True, timeout=60, env=env, check=False
    )


@pytest.mark.parametrize(('name', 'cls'), GETTERS.items())
def test_getter_returns_one_cached_instance_until_cache_clear(name, cls):
    getter = getattr(conf, name)
    first = getter()

    assert isinstance(first, cls)
    assert getter() is first

    getter.cache_clear()

    assert getter() is not first


def test_s3_names_are_deprecated_and_serve_the_same_objects():
    with pytest.warns(DeprecationWarning, match=r'conf\.S3Settings is deprecated.*1\.0') as record:
        cls = conf.S3Settings

    assert cls is conf._S3Settings  # noqa: SLF001
    assert cls.__name__ == 'S3Settings'
    assert record[0].filename == __file__

    with pytest.warns(DeprecationWarning, match=r'conf\.get_s3_settings is deprecated.*1\.0'):
        getter = conf.get_s3_settings

    assert getter is conf._get_s3_settings  # noqa: SLF001
    assert isinstance(getter(), cls)


def test_getter_reads_the_environment_on_first_call(monkeypatch):
    monkeypatch.setenv('DB_PORT', '6543')
    conf.get_db_settings.cache_clear()

    assert conf.get_db_settings().port == 6543


@pytest.mark.parametrize(('legacy', 'getter'), LEGACY.items())
def test_legacy_attribute_warns_naming_1_0_and_is_the_getter_instance(legacy, getter):
    with pytest.warns(DeprecationWarning, match=rf'conf\.{legacy} is deprecated.*1\.0.*{getter}\(\)') as record:
        value = getattr(conf, legacy)

    assert value is getattr(conf, '_' + getter if legacy == 's3_settings' else getter)()
    assert record[0].filename == __file__  # stacklevel points at the caller


def test_from_import_of_a_legacy_name_warns():
    with pytest.warns(DeprecationWarning, match='1.0'):
        from python3_commons.conf import db_settings  # noqa: PLC0415

    assert isinstance(db_settings, DBSettings)


def test_unknown_attribute_raises_attribute_error():
    with pytest.raises(AttributeError, match='no_such_name'):
        conf.no_such_name  # noqa: B018


def test_object_storage_settings_class_and_getter_do_not_warn(monkeypatch):
    monkeypatch.setenv('OBJECT_STORAGE_WORK_PATH', 's3://bucket/work/')
    conf.get_object_storage_settings.cache_clear()

    with warnings.catch_warnings():
        warnings.simplefilter('error')

        assert conf.get_object_storage_settings().work_path == 's3://bucket/work'
        assert ObjectStorageSettings().work_path == 's3://bucket/work'


def test_oidc_settings_has_no_legacy_attribute():
    assert not hasattr(conf, 'oidc_settings')
    assert conf.get_oidc_settings() is conf.get_oidc_settings()


def test_hermetic_fixture_starts_each_test_with_empty_caches():
    assert all(getattr(conf, name).cache_info().currsize == 0 for name in GETTERS)


def test_scrub_helper_lists_prefixed_and_named_variables(monkeypatch):
    monkeypatch.setenv('DB_HOST', 'x')
    monkeypatch.setenv('LOGGING_LEVEL', 'DEBUG')
    monkeypatch.setenv('UNRELATED', 'x')
    monkeypatch.setattr(settings_env, 'SCRUBBED_NAMES', ('EXTRA_NAME',))
    monkeypatch.setenv('EXTRA_NAME', 'x')

    names = settings_env.scrubbed_names()

    assert {'DB_HOST', 'LOGGING_LEVEL', 'EXTRA_NAME'} <= set(names)
    assert 'UNRELATED' not in names


@pytest.mark.parametrize('port', ['abc', 'tcp://10.0.0.5:5432'])
def test_bad_db_port_does_not_break_importing_the_modules(port, monkeypatch):
    monkeypatch.setenv('DB_PORT', port)
    result = _run('import python3_commons.auth, python3_commons.cache, python3_commons.api_client')

    assert result.returncode == 0, result.stderr


def test_import_emits_no_deprecation_warning():
    code = 'import python3_commons.api_client, python3_commons.audit, python3_commons.cache'
    result = _run(code, None, '-W', 'error::DeprecationWarning')

    assert result.returncode == 0, result.stderr


def test_import_of_the_oidc_settings_class_emits_no_warning():
    result = _run('from python3_commons.conf import OIDCSettings; OIDCSettings()', None, '-W', 'error')

    assert result.returncode == 0, result.stderr


def test_template_import_emits_one_warning_and_returns_working_settings():
    code = (
        'import warnings\n'
        'with warnings.catch_warnings(record=True) as caught:\n'
        '    warnings.simplefilter("always")\n'
        '    from python3_commons.conf import db_settings\n'
        'print(len(caught), caught[0].category.__name__, "1.0" in str(caught[0].message), '
        'type(db_settings).__name__, db_settings.port)\n'
    )
    result = _run(code)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['1', 'DeprecationWarning', 'True', 'DBSettings', '5432']
