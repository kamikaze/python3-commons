import uuid
from datetime import datetime

from fastapi_users_db_sqlalchemy import GUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, PrimaryKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from python3_commons.db import Base


class RBACRole(Base):
    __tablename__ = 'rbac_roles'

    uid: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)


class RBACPermission(Base):
    __tablename__ = 'rbac_permissions'

    uid: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    __table_args__ = (CheckConstraint("name ~ '^[a-z0-9_.]+$'", name='check_rbac_permissions_name'),)


class RBACRolePermission(Base):
    __tablename__ = 'rbac_role_permissions'

    role_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey('rbac_roles.uid', name='fk_rbac_role_permissions_role', ondelete='CASCADE'),
        index=True,
    )
    permission_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey('rbac_permissions.uid', name='fk_rbac_role_permissions_permission', ondelete='CASCADE'),
        index=True,
    )

    __table_args__ = (PrimaryKeyConstraint('role_uid', 'permission_uid', name='pk_rbac_role_permissions'),)


class RBACUserRole(Base):
    __tablename__ = 'rbac_user_roles'

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey('users.id', name='fk_rbac_user_roles_user', ondelete='CASCADE'),
        index=True,
    )
    role_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey('rbac_roles.uid', name='fk_rbac_user_roles_role', ondelete='CASCADE'),
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (PrimaryKeyConstraint('user_id', 'role_uid', name='pk_rbac_user_roles'),)


class RBACApiKeyRole(Base):
    __tablename__ = 'rbac_api_key_roles'

    api_key_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey('api_keys.uid', name='fk_rbac_api_key_roles_user', ondelete='CASCADE'),
        index=True,
    )
    role_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey('rbac_roles.uid', name='fk_rbac_api_key_roles_role', ondelete='CASCADE'),
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (PrimaryKeyConstraint('api_key_uid', 'role_uid', name='pk_rbac_api_key_roles'),)


# class RBACRoleRelation(Base):
#     __tablename__ = 'rbac_role_relations'
#
#     parent_uid: Mapped[uuid.UUID] = mapped_column(UUID)
#     child_uid: Mapped[uuid.UUID] = mapped_column(UUID)
#
#     __table_args__ = (
#         PrimaryKeyConstraint('parent_uid', 'child_uid', name='pk_rbac_role_relations'),
#     )
