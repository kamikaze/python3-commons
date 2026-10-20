from typing import TYPE_CHECKING, Any
from uuid import UUID

try:
    import sqlalchemy as sa
    from sqlalchemy import and_, exists, func
except ImportError as e:
    msg = 'Install python3-commons[authz] to use this feature'
    raise RuntimeError(msg) from e

from python3_commons.db.models.rbac import RBACApiKeyRole, RBACPermission, RBACRolePermission, RBACUserRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _has_permission(session: AsyncSession, role_model: Any, subject_filter: Any, permission: str) -> bool:
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
