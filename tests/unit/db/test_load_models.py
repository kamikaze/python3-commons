"""Registration of library models is explicit and FK-complete, also under -X lazy_imports=all (subprocesses)."""

import json
import subprocess
import sys

import pytest

EXPECTED = {
    'api_keys',
    'rbac_api_key_roles',
    'rbac_permissions',
    'rbac_role_permissions',
    'rbac_roles',
    'rbac_user_roles',
    'user_groups',
    'users',
}

ENTRY_POINTS = {
    'permissions': 'import python3_commons.permissions',
    'auth_module': 'import python3_commons.db.models.auth',
    'rbac_module': 'import python3_commons.db.models.rbac',
    'load_models': 'from python3_commons.db.models import load_models; load_models()',
}

SCRIPT = """
import json
{entry}
from python3_commons.db import metadata

tables = [t.name for t in metadata.sorted_tables]
for t in metadata.tables.values():
    for fk in t.foreign_keys:
        fk.column  # raises NoReferencedTableError if unresolved
print(json.dumps({{'tables': tables, 'fks': sum(len(t.foreign_keys) for t in metadata.tables.values())}}))
"""


@pytest.mark.parametrize('lazy', ['normal', 'all'])
@pytest.mark.parametrize('entry', ENTRY_POINTS)
def test_entry_point_yields_fk_complete_metadata(lazy: str, entry: str, request: pytest.FixtureRequest) -> None:
    if lazy == 'all' and sys.version_info < (3, 15):
        pytest.skip('no lazy imports (PEP 810) before Python 3.15: -X lazy_imports=all is ignored')

    if lazy == 'all':
        # SQLAlchemy 2.1.6 itself cannot create a declarative Base under -X lazy_imports=all:
        # sqlalchemy.orm.events fails to import ('function' object has no attribute 'Mapper'), so
        # python3_commons.db fails before any model is defined. Strict: flips when SQLAlchemy is fixed.
        request.applymarker(pytest.mark.xfail(reason='SQLAlchemy ORM is not lazy_imports=all safe', strict=True))

    result = subprocess.run(  # noqa: S603
        [sys.executable, '-X', f'lazy_imports={lazy}', '-c', SCRIPT.format(entry=ENTRY_POINTS[entry])],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data['tables']) == EXPECTED
    assert data['fks'] == 8
    # parents sort before children
    assert data['tables'].index('users') < data['tables'].index('api_keys') < data['tables'].index('rbac_api_key_roles')


def test_load_models_returns_shared_metadata() -> None:
    from python3_commons.db import metadata  # noqa: PLC0415
    from python3_commons.db.models import load_models  # noqa: PLC0415

    assert load_models() is metadata
    assert set(metadata.tables) >= EXPECTED
