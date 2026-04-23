import uuid
from datetime import datetime

try:
    from sqlalchemy import UUID, DateTime, ForeignKey, String
    from sqlalchemy.orm import Mapped, mapped_column
except ImportError as e:
    msg = 'Install python3_commons[database] to use this feature'
    raise RuntimeError(msg) from e

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
