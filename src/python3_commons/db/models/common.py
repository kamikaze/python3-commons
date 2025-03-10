from pydantic import AwareDatetime
from sqlalchemy import (
    DateTime, BIGINT
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import expression
from sqlalchemy.sql.ddl import CreateColumn


class UTCNow(expression.FunctionElement):
    type = DateTime(timezone=True)


@compiles(UTCNow, 'postgresql')
def pg_utcnow(element, compiler, **kw):
    return "TIMEZONE('utc', CURRENT_TIMESTAMP)"


@compiles(CreateColumn, 'postgresql')
def use_identity(element, compiler, **kw):
    result = compiler.visit_create_column(element, **kw).replace('SERIAL', 'INT GENERATED BY DEFAULT AS IDENTITY')

    return result.replace('BIGSERIAL', 'BIGINT GENERATED BY DEFAULT AS IDENTITY')


class BaseDBModel:
    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    created_at: Mapped[AwareDatetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTCNow())
    updated_at: Mapped[AwareDatetime] = mapped_column(DateTime(timezone=True), onupdate=UTCNow())


class BaseDBUUIDModel:
    uid: Mapped[UUID] = mapped_column(UUID, primary_key=True)
    created_at: Mapped[AwareDatetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=UTCNow())
    updated_at: Mapped[AwareDatetime | None] = mapped_column(DateTime(timezone=True), onupdate=UTCNow())
