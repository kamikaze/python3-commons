"""The public API that downstream projects use, pinned against tests/contract/snapshot.py."""

import importlib
import inspect
import json
import logging
import os
import pkgutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from pydantic import HttpUrl

from python3_commons.auth import OIDCClient, TokenData
from python3_commons.conf import OIDCSettings
from tests.contract import snapshot

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
# modules whose star-import may legitimately load the optional SOAP stack
_ZEEP_MODULES = frozenset({'python3_commons.zeep_audit', 'python3_commons.soap_client'})


@pytest.mark.parametrize('path', snapshot.IMPORTED_NAMES)
def test_imported_name_resolves(path: str) -> None:
    assert pkgutil.resolve_name(path) is not None


@pytest.mark.parametrize('path', snapshot.DEPRECATED_NAMES)
def test_deprecated_name_resolves_with_a_warning(path: str) -> None:
    with pytest.warns(DeprecationWarning, match='1.0'):
        assert pkgutil.resolve_name(path) is not None


@pytest.mark.parametrize('path', snapshot.DICTCONFIG_FACTORIES)
def test_dictconfig_factory_resolves(path: str) -> None:
    assert callable(pkgutil.resolve_name(path))


def test_dictconfig_accepts_the_template_entries(apply_dict_config: Callable[[dict[str, Any]], None]) -> None:
    apply_dict_config(
        {
            'version': 1,
            'formatters': {'json': {'()': snapshot.DICTCONFIG_FACTORIES[0]}},
            'filters': {'info_and_below': {'()': snapshot.DICTCONFIG_FACTORIES[1], 'level': 'INFO'}},
            'handlers': {
                'console': {
                    'class': 'logging.StreamHandler',
                    'formatter': 'json',
                    'filters': ['info_and_below'],
                }
            },
            'root': {'handlers': ['console'], 'level': 'INFO'},
        }
    )
    console = logging.getLogger().handlers[-1]

    assert type(console.formatter).__name__ == 'JSONFormatter'
    assert console.filters


def test_oidc_client_init_signature() -> None:
    assert str(inspect.signature(OIDCClient.__init__)) == snapshot.OIDC_CLIENT_INIT_SIGNATURE


def test_oidc_client_get_jwks_signature() -> None:
    assert str(inspect.signature(OIDCClient.get_jwks)) == snapshot.OIDC_CLIENT_GET_JWKS_SIGNATURE


def test_oidc_client_from_settings_signature() -> None:
    assert str(inspect.signature(OIDCClient.from_settings)) == snapshot.OIDC_CLIENT_FROM_SETTINGS_SIGNATURE


def test_get_jwks_can_be_replaced_on_an_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OIDCClient(HttpUrl('https://issuer.example'), 'client-id')

    async def fake_get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
        return {'keys': [], 'force_refresh': force_refresh}

    monkeypatch.setattr(client, 'get_jwks', fake_get_jwks)

    assert client.get_jwks is fake_get_jwks


def test_oidc_settings_instance_accepts_setattr(monkeypatch: pytest.MonkeyPatch) -> None:
    oidc_settings = OIDCSettings()

    monkeypatch.setattr(oidc_settings, 'audience', 'api')
    monkeypatch.setattr(oidc_settings, 'issuer', 'https://issuer.example')
    monkeypatch.setattr(oidc_settings, 'leeway', 5)

    assert (oidc_settings.audience, oidc_settings.issuer, oidc_settings.leeway) == ('api', 'https://issuer.example', 5)


def test_token_data_is_a_msgspec_struct() -> None:
    class AppTokenData(TokenData):
        groups: list[str] = msgspec.field(default_factory=list)

    payload = {
        'exp': 2,
        'iat': 1,
        'iss': 'https://issuer.example',
        'sub': 'user-1',
        'groups': ['a'],
        'realm_access': {'roles': ['admin']},
    }
    token = msgspec.convert(payload, type=AppTokenData)

    assert token.sub == 'user-1'
    assert token.groups == ['a']
    assert token.roles == ['admin']


@pytest.mark.parametrize('module_name', sorted(snapshot.PUBLIC_ALL))
def test_all_matches_snapshot(module_name: str) -> None:
    module = importlib.import_module(module_name)

    assert set(module.__all__) == snapshot.PUBLIC_ALL[module_name]
    assert len(module.__all__) == len(set(module.__all__))


@pytest.mark.parametrize('module_name', sorted(snapshot.PUBLIC_ALL))
def test_all_names_resolve_without_a_warning(module_name: str) -> None:
    module = importlib.import_module(module_name)
    moved = snapshot.STAR_IMPORT_DEPRECATED.get(module_name, frozenset())

    for name in module.__all__:
        if name in moved:
            continue

        assert hasattr(module, name), name


def test_helpers_moved_names_warn_naming_1_0() -> None:
    module = importlib.import_module('python3_commons.helpers')

    for name in sorted(snapshot.HELPERS_MOVED_NAMES):
        with pytest.warns(DeprecationWarning, match='1.0'):
            assert hasattr(module, name), name


@pytest.mark.parametrize('module_name', sorted(snapshot.PUBLIC_ALL))
def test_star_import_is_silent_and_binds_all(module_name: str) -> None:
    # helpers (3.D1) and async_functools (3.C) keep deprecated names in __all__: their star-import warns per name
    moved = snapshot.STAR_IMPORT_DEPRECATED.get(module_name, frozenset())
    code = (
        'import json, sys\n'
        'before = set(globals())\n'
        f'from {module_name} import *\n'
        'bound = sorted(set(globals()) - before - {"before", "__warningregistry__"})\n'
        'loaded = [m for m in ("zeep", "lxml") if m in sys.modules]\n'
        'print(json.dumps([bound, loaded]))'
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-W', 'always::DeprecationWarning' if moved else 'error', '-c', code],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr.count('DeprecationWarning') == len(moved), result.stderr

    bound, loaded = json.loads(result.stdout)

    assert set(bound) == snapshot.PUBLIC_ALL[module_name]
    assert module_name in _ZEEP_MODULES or loaded == []


@pytest.mark.parametrize('module_name', ['python3_commons.serializers.msgpack', 'python3_commons.db.helpers'])
def test_a10_modules_have_no_all(module_name: str) -> None:
    assert not hasattr(importlib.import_module(module_name), '__all__')


def test_wheel_ships_py_typed(tmp_path: Path) -> None:
    env = {**os.environ, 'SETUPTOOLS_SCM_PRETEND_VERSION': os.environ.get('SETUPTOOLS_SCM_PRETEND_VERSION', '0.0.1')}
    subprocess.run(  # noqa: S603
        ['uv', 'build', '--wheel', '-o', str(tmp_path)],  # noqa: S607
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    [wheel] = tmp_path.glob('*.whl')

    with zipfile.ZipFile(wheel) as archive:
        assert 'python3_commons/py.typed' in archive.namelist()
