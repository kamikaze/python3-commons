from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from python3_commons.db import Base
from python3_commons.db.models.common import BaseDBModel


class UserGroup(BaseDBModel, Base):
    __tablename__ = 'user_groups'

    name: Mapped[str] = mapped_column(String, nullable=False)
