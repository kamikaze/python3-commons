"""Type-checked only, never imported or collected by pytest: mirrors how downstream projects use the library.

``ty check`` and ``pyright`` must report 0 errors for this file (see the typecheck job in .github/workflows/checks.yml).
"""

from typing import TYPE_CHECKING

import msgspec
from pydantic import HttpUrl
from pydantic_settings import SettingsConfigDict
from sqlalchemy import Column, String

from python3_commons.audit import ZeepAuditPlugin
from python3_commons.auth import OIDCClient, TokenData
from python3_commons.conf import CommonSettings, DBSettings, OIDCSettings, StringSeq, db_settings, get_db_settings
from python3_commons.db import AsyncSessionManager, Base
from python3_commons.db.models.common import BaseDBUUIDModel
from python3_commons.decorators import log_execution_time, retry
from python3_commons.exceptions import AppError

if TYPE_CHECKING:
    from sqlalchemy import MetaData


class AppTokenData(TokenData):
    groups: list[str] = msgspec.field(default_factory=list)

    @property
    def roles(self) -> list[str]:
        return [*super().roles, *self.groups]


class AppSettings(CommonSettings):
    allowed_origins: StringSeq = ('*',)


class RODBSettings(DBSettings):
    model_config = SettingsConfigDict(env_prefix='RO_DB_')


class UserProfile(BaseDBUUIDModel, Base):
    __tablename__ = 'user_profiles'

    nickname = Column(String)


def use_library() -> None:
    dsn = db_settings.dsn
    shared_dsn = get_db_settings().dsn
    configs = {'main': db_settings, 'ro': RODBSettings()}
    manager = AsyncSessionManager(configs)
    metadata: MetaData = Base.metadata
    oidc_settings = OIDCSettings()
    oidc_settings.leeway = 5
    client = OIDCClient(HttpUrl('https://issuer.example'), 'client-id', timeout=5.0, verify_cert=True)
    shared_client = OIDCClient.from_settings(oidc_settings, timeout=5.0)
    default_client: OIDCClient = OIDCClient.from_settings()
    plugin = ZeepAuditPlugin('billing')
    token = msgspec.convert({}, type=AppTokenData)

    roles: list[str] = token.roles
    error = AppError('failure')

    print(  # noqa: T201
        dsn,
        shared_dsn,
        manager,
        metadata,
        client,
        plugin,
        roles,
        error,
        shared_client,
        default_client,
        token.has_role('admin'),
        AppSettings().allowed_origins,
    )


@log_execution_time
async def timed_async(value: int) -> int:
    return value


@log_execution_time(level=10)
def timed_sync(value: int) -> int:
    return value


@retry(attempts=2, retry_on=(ConnectionError, InterruptedError))
async def retried_async(value: int) -> str:
    return str(value)


@retry()
def retried_sync(value: int) -> str:
    return str(value)


async def use_decorators() -> None:
    number: int = await timed_async(1)
    other: int = timed_sync(2)
    text: str = await retried_async(3)
    text_sync: str = retried_sync(4)
    print(number, other, text, text_sync)  # noqa: T201
