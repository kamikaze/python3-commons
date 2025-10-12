import uuid

from sqlalchemy import CheckConstraint, ForeignKey, PrimaryKeyConstraint, String
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
