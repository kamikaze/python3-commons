import uuid
from datetime import datetime

from sqlalchemy import UUID, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from python3_commons.db import Base
from python3_commons.db.models.common import BaseDBUUIDModel


class ApiKey(BaseDBUUIDModel, Base):
    __tablename__ = 'api_keys'

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_uid: Mapped[uuid.UUID | None] = mapped_column(
        UUID,
        ForeignKey('users.uid', name='fk_api_keys_user', ondelete='RESTRICT'),
        index=True,
    )
    name: Mapped[str] = mapped_column(String, unique=True)
