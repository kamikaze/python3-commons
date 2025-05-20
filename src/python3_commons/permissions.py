import logging
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import and_, exists, func
from sqlalchemy.ext.asyncio import AsyncSession

from python3_commons.db.models import RBACApiKeyRole, RBACPermission, RBACRolePermission, RBACUserRole

logger = logging.getLogger(__name__)


async def has_api_key_permission(session: AsyncSession, api_key_uid: UUID, permission: str) -> bool:
    query = sa.select(
        exists().where(
            and_(
                RBACApiKeyRole.api_key_uid == api_key_uid,
                (RBACApiKeyRole.expires_at.is_(None) | (RBACApiKeyRole.expires_at > func.now())),
                RBACApiKeyRole.role_uid == RBACRolePermission.role_uid,
                RBACRolePermission.permission_uid == RBACPermission.uid,
                RBACPermission.name == permission,
            )
        )
    )

    cursor = await session.execute(query)
    result = cursor.scalar()

    return result


async def has_user_permission(session: AsyncSession, user_id: UUID, permission: str) -> bool:
    query = sa.select(
        exists().where(
            and_(
                RBACUserRole.user_id == user_id,
                (RBACUserRole.expires_at.is_(None) | (RBACUserRole.expires_at > func.now())),
                RBACUserRole.role_uid == RBACRolePermission.role_uid,
                RBACRolePermission.permission_uid == RBACPermission.uid,
                RBACPermission.name == permission,
            )
        )
    )

    cursor = await session.execute(query)
    result = cursor.scalar()

    return result
