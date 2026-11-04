import subprocess
import sys

import pytest

from python3_commons._extras import require

# module -> (extra named in the message, top-level dependency to hide)
GUARDED = {
    'python3_commons.api_client': ('api-client', 'aiohttp'),
    'python3_commons.auth': ('authn', 'aiohttp'),
    'python3_commons.cache': ('cache', 'valkey'),
    'python3_commons.object_storage': ('object-storage', 'object_storage_client'),
    'python3_commons.permissions': ('authz', 'sqlalchemy'),
    'python3_commons.soap_client': ('soap-client', 'zeep'),
    'python3_commons.zeep_audit': ('audit', 'zeep'),
    'python3_commons.db': ('database', 'sqlalchemy'),
    'python3_commons.db.models.auth': ('database', 'sqlalchemy'),
    'python3_commons.db.models.common': ('database', 'sqlalchemy'),
    'python3_commons.db.models.rbac': ('database', 'sqlalchemy'),
    'python3_commons.db.models.users': ('database', 'sqlalchemy'),
}


def test_require_passes_for_installed_modules():
    require('x', 'sys', 'json')


def test_require_names_the_extra_when_a_module_is_missing():
    with pytest.raises(RuntimeError, match=r'^Install python3-commons\[my-extra\] to use this feature$'):
        require('my-extra', 'json', 'no_such_module_for_python3_commons')


def test_require_treats_a_blocked_module_as_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, 'json', None)

    with pytest.raises(RuntimeError, match=r'Install python3-commons\[x\] to use this feature'):
        require('x', 'json')


@pytest.mark.parametrize(('module', 'extra', 'dependency'), [(m, *v) for m, v in GUARDED.items()])
def test_missing_extra_is_a_runtime_error(module, extra, dependency):
    script = (
        'import sys\n'
        f'sys.modules[{dependency!r}] = None\n'
        'try:\n'
        f'    import {module}\n'
        'except BaseException as e:\n'
        "    print(type(e).__name__, '|', e)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script], capture_output=True, text=True, timeout=60, check=True
    )

    assert result.stdout.strip() == f'RuntimeError | Install python3-commons[{extra}] to use this feature'


@pytest.mark.parametrize(
    ('module', 'dependency', 'first_use', 'message'),
    [
        # an eager import: the module fails when it is imported
        ('python3_commons.object_storage', 'object_storage_client', '', 'broken object_storage_client'),
        # `__lazy_modules__` (3.15): valkey is imported on first use of one of its names; 3.14 imports it eagerly
        ('python3_commons.cache', 'valkey', 'python3_commons.cache.Valkey', 'broken valkey'),
    ],
)
def test_installed_but_broken_dependency_raises_its_own_error(tmp_path, module, dependency, first_use, message):
    (tmp_path / dependency).mkdir()
    (tmp_path / dependency / '__init__.py').write_text(f'raise ImportError("{message}")\n')
    script = (
        'import sys\n'
        f'sys.path.insert(0, {str(tmp_path)!r})\n'
        'try:\n'
        f'    import {module}\n'
        f'    {first_use or "pass"}\n'
        'except BaseException as e:\n'
        "    print(type(e).__name__, '|', e)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-c', script], capture_output=True, text=True, timeout=60, check=True
    )

    assert result.stdout.strip() == f'ImportError | {message}'
