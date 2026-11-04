# pyright: strict
from uuid import UUID

from python3_commons._extras import require

require('authz', 'sqlalchemy')

import sqlalchemy as sa
from sqlalchemy import ColumnElement, and_, exists, func
from sqlalchemy.ext.asyncio import AsyncSession

from python3_commons.db.models import load_models
from python3_commons.db.models.rbac import RBACApiKeyRole, RBACPermission, RBACRolePermission, RBACUserRole

__all__ = [
    'has_api_key_permission',
    'has_user_permission',
]


async def _has_permission(
    session: AsyncSession,
    role_model: type[RBACUserRole | RBACApiKeyRole],
    subject_filter: ColumnElement[bool],
    permission: str,
) -> bool:
    load_models()

    query = sa.select(
        exists().where(
            and_(
                subject_filter,
                (role_model.expires_at.is_(None) | (role_model.expires_at > func.now())),
                role_model.role_uid == RBACRolePermission.role_uid,
                RBACRolePermission.permission_uid == RBACPermission.uid,
                RBACPermission.name == permission,
            )
        )
    )

    return bool(await session.scalar(query))


async def has_api_key_permission(session: AsyncSession, api_key_uid: UUID, permission: str) -> bool:
    return await _has_permission(session, RBACApiKeyRole, RBACApiKeyRole.api_key_uid == api_key_uid, permission)


async def has_user_permission(session: AsyncSession, user_id: UUID, permission: str) -> bool:
    return await _has_permission(session, RBACUserRole, RBACUserRole.user_id == user_id, permission)
