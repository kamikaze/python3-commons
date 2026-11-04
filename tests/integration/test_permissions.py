"""PostgreSQL checks of has_user_permission / has_api_key_permission and create_all after load_models().

Only active, non-expiring grants are covered; the time windows (starts_at, ApiKey.expires_at) are a known issue (S4).
Every test runs in its own schema, so it does not collide with the other integration tests.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from python3_commons.db import metadata
from python3_commons.db.models import (
    ApiKey,
    RBACApiKeyRole,
    RBACPermission,
    RBACRole,
    RBACRolePermission,
    RBACUserRole,
    User,
    load_models,
)
from python3_commons.permissions import has_api_key_permission, has_user_permission

_EXPECTED_TABLES = {
    'api_keys',
    'rbac_api_key_roles',
    'rbac_permissions',
    'rbac_role_permissions',
    'rbac_roles',
    'rbac_user_roles',
    'user_groups',
    'users',
}


@pytest_asyncio.fixture
async def schema_engine(pg_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    schema = f'perm_{uuid.uuid4().hex}'

    async with pg_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA {schema}'))

    engine = create_async_engine(pg_engine.url, connect_args={'server_settings': {'search_path': schema}})

    try:
        yield engine
    finally:
        await engine.dispose()

        async with pg_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


@dataclass
class _Grants:
    user_id: uuid.UUID
    api_key_uid: uuid.UUID
    user_without_roles: uuid.UUID
    api_key_without_roles: uuid.UUID
    sessions: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def grants(schema_engine: AsyncEngine) -> _Grants:
    load_models()

    async with schema_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)

    sessions = async_sessionmaker(schema_engine, expire_on_commit=False)
    started = datetime.now(UTC) - timedelta(days=1)
    user_id, bare_user_id = uuid.uuid4(), uuid.uuid4()
    key_uid, bare_key_uid = uuid.uuid4(), uuid.uuid4()
    reader_role, other_role = uuid.uuid4(), uuid.uuid4()
    read_perm, write_perm, admin_perm = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async with sessions() as session:
        session.add_all([User(uid=user_id, username='alice'), User(uid=bare_user_id, username='bob')])
        await session.flush()
        session.add_all(
            [
                ApiKey(uid=key_uid, name='key-1', user_uid=user_id),
                ApiKey(uid=bare_key_uid, name='key-2', user_uid=None),
                RBACRole(uid=reader_role, name='reader'),
                RBACRole(uid=other_role, name='other'),
                RBACPermission(uid=read_perm, name='docs.read'),
                RBACPermission(uid=write_perm, name='docs.write'),
                RBACPermission(uid=admin_perm, name='admin'),
            ]
        )
        await session.flush()
        session.add_all(
            [
                RBACRolePermission(role_uid=reader_role, permission_uid=read_perm),
                RBACRolePermission(role_uid=reader_role, permission_uid=write_perm),
                RBACRolePermission(role_uid=other_role, permission_uid=admin_perm),
                RBACUserRole(user_id=user_id, role_uid=reader_role, starts_at=started, expires_at=None),
                RBACApiKeyRole(api_key_uid=key_uid, role_uid=reader_role, starts_at=started, expires_at=None),
            ]
        )
        await session.commit()

    return _Grants(user_id, key_uid, bare_user_id, bare_key_uid, sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize(('permission', 'expected'), [('docs.read', True), ('docs.write', True), ('admin', False)])
async def test_user_permission_follows_the_granted_roles(grants: _Grants, permission: str, *, expected: bool) -> None:
    async with grants.sessions() as session:
        assert await has_user_permission(session, grants.user_id, permission) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(('permission', 'expected'), [('docs.read', True), ('docs.write', True), ('admin', False)])
async def test_api_key_permission_follows_the_granted_roles(
    grants: _Grants, permission: str, *, expected: bool
) -> None:
    async with grants.sessions() as session:
        assert await has_api_key_permission(session, grants.api_key_uid, permission) is expected


@pytest.mark.asyncio
async def test_missing_permission_name_is_denied(grants: _Grants) -> None:
    async with grants.sessions() as session:
        assert await has_user_permission(session, grants.user_id, 'does.not.exist') is False
        assert await has_api_key_permission(session, grants.api_key_uid, 'does.not.exist') is False


@pytest.mark.asyncio
async def test_subject_without_any_role_is_denied(grants: _Grants) -> None:
    async with grants.sessions() as session:
        assert await has_user_permission(session, grants.user_without_roles, 'docs.read') is False
        assert await has_api_key_permission(session, grants.api_key_without_roles, 'docs.read') is False


@pytest.mark.asyncio
async def test_unknown_user_and_unknown_api_key_are_denied(grants: _Grants) -> None:
    async with grants.sessions() as session:
        assert await has_user_permission(session, uuid.uuid4(), 'docs.read') is False
        assert await has_api_key_permission(session, uuid.uuid4(), 'docs.read') is False


@pytest.mark.asyncio
async def test_a_user_grant_does_not_leak_to_an_api_key_and_back(grants: _Grants) -> None:
    async with grants.sessions() as session:
        assert await has_api_key_permission(session, grants.user_id, 'docs.read') is False
        assert await has_user_permission(session, grants.api_key_uid, 'docs.read') is False


@pytest.mark.asyncio
async def test_create_all_after_load_models_creates_every_library_table(schema_engine: AsyncEngine) -> None:
    assert load_models() is metadata

    async with schema_engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        tables = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        foreign_keys = await conn.run_sync(
            lambda sync_conn: {fk['name'] for fk in inspect(sync_conn).get_foreign_keys('rbac_user_roles')}
        )

    assert tables == _EXPECTED_TABLES
    assert foreign_keys == {'fk_rbac_user_roles_user', 'fk_rbac_user_roles_role'}
