"""Modules that list their optional dependency in `__lazy_modules__` (PEP 810) do not import it at import time."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contract import snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]

# module -> top-level packages that must stay unloaded after importing it (3.15 only: 3.14 ignores `__lazy_modules__`)
LAZY: dict[str, tuple[str, ...]] = {
    'python3_commons.api_client': ('aiohttp', 'multidict', 'yarl'),
    'python3_commons.auth': ('aiohttp', 'multidict', 'yarl'),
    'python3_commons.cache': ('valkey',),
}

SCRIPT = """
import json, sys
import {module}
print(json.dumps([name for name in {packages!r} if name in sys.modules]))
"""


@pytest.mark.skipif(sys.version_info < (3, 15), reason='PEP 810 lazy imports need Python 3.15')
@pytest.mark.parametrize('mode', ['normal', 'all'])
@pytest.mark.parametrize('module', sorted(LAZY))
def test_optional_dependency_is_not_imported_with_the_module(module: str, mode: str) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, '-X', f'lazy_imports={mode}', '-c', SCRIPT.format(module=module, packages=LAZY[module])],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


# Third-party packages that are not safe under -X lazy_imports=all (3.15), so the module fails on its own: expected
# failures until the package is fixed. SQLAlchemy 2.1.6: "No such event 'before_configured'" when a declarative Base
# is built; zeep 4.3.3 (through httpx): `Version(httpx.__version__)` receives the lazily imported submodule.
_ALL_MODE_BLOCKERS: dict[str, str] = {
    'python3_commons.db': 'SQLAlchemy ORM is not lazy_imports=all safe',
    'python3_commons.db.models': 'SQLAlchemy ORM is not lazy_imports=all safe',
    'python3_commons.db.models.auth': 'SQLAlchemy ORM is not lazy_imports=all safe',
    'python3_commons.db.models.common': 'SQLAlchemy ORM is not lazy_imports=all safe',
    'python3_commons.db.models.rbac': 'SQLAlchemy ORM is not lazy_imports=all safe',
    'python3_commons.db.models.users': 'SQLAlchemy ORM is not lazy_imports=all safe',
    'python3_commons.soap_client': 'zeep.transports is not lazy_imports=all safe (httpx.__version__)',
}
_MODULES = sorted(set(snapshot.PUBLIC_ALL) - {'python3_commons'})


@pytest.mark.parametrize('mode', ['normal', 'all'])
@pytest.mark.parametrize('module', _MODULES)
def test_module_imports_and_resolves_its_names(module: str, mode: str, request: pytest.FixtureRequest) -> None:
    """Whatever the interpreter does with `__lazy_modules__`, every public name resolves (also on 3.14)."""
    if mode == 'all' and sys.version_info < (3, 15):
        pytest.skip('no lazy imports (PEP 810) before Python 3.15: -X lazy_imports=all is ignored')

    if mode == 'all' and module in _ALL_MODE_BLOCKERS:
        request.applymarker(pytest.mark.xfail(reason=_ALL_MODE_BLOCKERS[module], strict=True))

    moved = snapshot.STAR_IMPORT_DEPRECATED.get(module, frozenset())
    script = f'import {module} as m\nfor name in set(m.__all__) - {set(moved)!r}:\n    getattr(m, name)\n'
    cmd = [sys.executable, '-W', 'error'] + (['-X', 'lazy_imports=all'] if mode == 'all' else []) + ['-c', script]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=REPO_ROOT, timeout=60)  # noqa: S603

    assert result.returncode == 0, result.stderr
