import uuid

from sqlalchemy import UUID, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from python3_commons.db import Base
from python3_commons.db.models.common import BaseDBUUIDModel


class UserGroup(BaseDBUUIDModel, Base):
    __tablename__ = 'user_groups'

    name: Mapped[str] = mapped_column(String, nullable=False)


class User(BaseDBUUIDModel, Base):
    __tablename__ = 'users'

    group_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey('user_groups.uid', ondelete='RESTRICT'), index=True
    )
    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
