import uuid
from datetime import datetime

from fastapi_users_db_sqlalchemy import GUID, SQLAlchemyBaseUserTableUUID
from sqlalchemy import BIGINT, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from python3_commons.db import Base
from python3_commons.db.models.common import BaseDBModel, BaseDBUUIDModel


class UserGroup(BaseDBModel, Base):
    __tablename__ = 'user_groups'

    name: Mapped[str] = mapped_column(String, nullable=False)


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = 'users'

    username: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    group_id: Mapped[int | None] = mapped_column(BIGINT, ForeignKey('user_groups.id'))


class ApiKey(BaseDBUUIDModel, Base):
    __tablename__ = 'api_keys'

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey('users.id', name='fk_api_key_user', ondelete='RESTRICT'),
        index=True,
    )
    partner_name: Mapped[str] = mapped_column(String, unique=True)
    key: Mapped[str] = mapped_column(String, unique=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
