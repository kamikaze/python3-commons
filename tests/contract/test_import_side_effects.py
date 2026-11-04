"""Importing a public module has no side effects (4.A): no heavy optional dependency, no settings, no handlers.

Every check runs in a fresh interpreter, under `-X lazy_imports=normal` and (3.15 only) `-X lazy_imports=all`, with
the settings environment either absent or garbage: an import must not read it. Foreign DDL is deliberately not
checked (the IDENTITY hook stays, A7).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contract import snapshot
from tests.contract.test_lazy_imports import _ALL_MODE_BLOCKERS
from tests.settings_env import scrubbed_names

REPO_ROOT = Path(__file__).resolve().parents[2]
_MODES = [
    'normal',
    pytest.param(
        'all', marks=pytest.mark.skipif(sys.version_info < (3, 15), reason='PEP 810 lazy imports need Python 3.15')
    ),
]
_MODULES = sorted(snapshot.PUBLIC_ALL)
_GARBAGE_ENV = {
    'DB_DSN': 'not a dsn',
    'DB_POOL_SIZE': 'many',
    'VALKEY_DSN': 'not a dsn',
    'VALKEY_MAX_CONNECTIONS': 'many',
    'OIDC_ENABLED': 'perhaps',
    'OIDC_CLIENT_ID': '\u202e\ufffd',
    'S3_ENDPOINT_URL': 'not a url',
    'OBJECT_STORAGE_URL': 'not a url',
    'API_CLIENT_TIMEOUT': 'soon',
    'LOGGING_LEVEL': 'LOUD',
    'OSC_KEYRING_FILE': '/nonexistent/keyring',
}
# Heavy or optional packages that importing auth or api_client must leave unloaded
_DENYLIST = ('zeep', 'lxml', 'requests', 'sqlalchemy', 'asyncpg', 'valkey', 'object_storage_client')

_DENYLIST_SCRIPT = """
import json, sys
import {module}
print(json.dumps([name for name in {packages!r} if name in sys.modules]))
"""

# The audit hook records what an import touches: the keyring file is the one that must never be opened. BaseSettings
# construction and Logger.addHandler are wrapped (they are not audited events) before the module is imported.
_SIDE_EFFECTS_SCRIPT = """
import json, logging, os, sys

keyring = os.environ.get('OSC_KEYRING_FILE', '')
events = []


def hook(event, args):
    if event == 'open' and keyring and args and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.fsdecode(args[0]) == keyring:
            events.append(f'keyring opened: {{os.fsdecode(args[0])}}')
    elif event in ('socket.connect', 'subprocess.Popen', 'os.system'):
        events.append(f'{{event}} {{args!r}}'[:120])


sys.addaudithook(hook)

import pydantic_settings

settings = []
original_init = pydantic_settings.BaseSettings.__init__


def init(self, *args, **kwargs):
    settings.append(f'{{type(self).__module__}}.{{type(self).__qualname__}}')
    original_init(self, *args, **kwargs)


pydantic_settings.BaseSettings.__init__ = init

added = []
original_add_handler = logging.Logger.addHandler


def add_handler(self, handler):
    if not isinstance(handler, logging.NullHandler):  # third-party libraries install these by convention
        added.append(f'{{self.name}}: {{handler!r}}')
    original_add_handler(self, handler)


logging.Logger.addHandler = add_handler


def handlers():
    named = [lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    return sorted(
        f'{{lg.name}}: {{h!r}}'
        for lg in [logging.getLogger(), *named]
        for h in lg.handlers
        if not isinstance(h, logging.NullHandler)
    )


before = handlers()
import {module} as imported

for name in set(imported.__all__) - {moved!r}:  # resolve what lazy imports defer, so lazy_imports=all sees it too
    getattr(imported, name)

storage = sys.modules.get('python3_commons.object_storage')
client_built = storage is not None and storage.default_client.cache_info().currsize > 0
print(json.dumps({{
    'settings': settings,
    'handlers_added': added,
    'handlers_new': sorted(set(handlers()) - set(before)),
    'object_storage_client_built': client_built,
    'events': events,
}}))
"""

_FK_SCRIPT = """
import json
from python3_commons.db import metadata
from python3_commons.db.models import load_models

assert load_models() is metadata
unresolved = []

for table in metadata.tables.values():
    for fk in table.foreign_keys:
        try:
            target = fk.column.table
        except Exception as e:
            unresolved.append(f'{{table.name}}: {{fk.target_fullname}} ({{type(e).__name__}})')
        else:
            if metadata.tables.get(target.key) is not target:
                unresolved.append(f'{{table.name}}: {{fk.target_fullname}} is outside the metadata')

print(json.dumps({{'tables': sorted(metadata.tables), 'unresolved': unresolved}}))
"""


def _run(script: str, mode: str, *, environment: str = 'absent') -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in scrubbed_names()}

    if environment == 'garbage':
        env.update(_GARBAGE_ENV)

    cmd = [sys.executable, '-W', 'error', *(['-X', 'lazy_imports=all'] if mode == 'all' else []), '-c', script]

    return subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=REPO_ROOT, env=env, timeout=60)  # noqa: S603


def _expect_all_mode_blocker(module: str, mode: str, request: pytest.FixtureRequest) -> None:
    if mode == 'all' and module in _ALL_MODE_BLOCKERS:
        request.applymarker(pytest.mark.xfail(reason=_ALL_MODE_BLOCKERS[module], strict=True))


@pytest.mark.parametrize('mode', _MODES)
@pytest.mark.parametrize('module', ['python3_commons.auth', 'python3_commons.api_client'])
def test_heavy_optional_packages_stay_unloaded(module: str, mode: str) -> None:
    result = _run(_DENYLIST_SCRIPT.format(module=module, packages=_DENYLIST), mode)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize('environment', ['absent', 'garbage'])
@pytest.mark.parametrize('mode', _MODES)
@pytest.mark.parametrize('module', _MODULES)
def test_import_has_no_side_effects(module: str, mode: str, environment: str, request: pytest.FixtureRequest) -> None:
    _expect_all_mode_blocker(module, mode, request)

    moved = set(snapshot.STAR_IMPORT_DEPRECATED.get(module, frozenset()))
    result = _run(_SIDE_EFFECTS_SCRIPT.format(module=module, moved=moved), mode, environment=environment)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        'settings': [],
        'handlers_added': [],
        'handlers_new': [],
        'object_storage_client_built': False,
        'events': [],
    }


@pytest.mark.parametrize('mode', _MODES)
def test_metadata_is_foreign_key_complete_after_load_models(mode: str, request: pytest.FixtureRequest) -> None:
    _expect_all_mode_blocker('python3_commons.db.models', mode, request)

    result = _run(_FK_SCRIPT.format(), mode)

    assert result.returncode == 0, result.stderr

    report = json.loads(result.stdout)

    assert report['tables']
    assert report['unresolved'] == []
