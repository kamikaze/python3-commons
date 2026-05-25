import contextlib
import logging
from collections.abc import AsyncGenerator, Callable, Mapping
from typing import TYPE_CHECKING

try:
    from sqlalchemy import MetaData
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_engine_from_config
    from sqlalchemy.ext.asyncio.session import async_sessionmaker
    from sqlalchemy.orm import declarative_base
except ImportError as e:
    msg = 'Install python3-commons[database] to use this feature'
    raise RuntimeError(msg) from e

if TYPE_CHECKING:
    from python3_commons.conf import DBSettings

logger = logging.getLogger(__name__)
metadata = MetaData()
Base = declarative_base(metadata=metadata)


class AsyncSessionManager:
    def __init__(self, db_configs: Mapping[str, DBSettings]) -> None:
        self.db_configs: Mapping[str, DBSettings] = db_configs
        self.engines: dict[str, AsyncEngine] = {}
        self.session_makers: dict = {}

    def get_db_config(self, name: str) -> DBSettings:
        try:
            return self.db_configs[name]
        except KeyError:
            logger.exception('Missing database settings: %s', name)

            raise

    def async_engine_from_db_config(self, name):
        db_config = self.get_db_config(name)
        configuration = {
            'url': str(db_config.dsn),
            'echo': db_config.echo,
            'pool_size': db_config.pool_size,
            'max_overflow': db_config.max_overflow,
            'pool_timeout': db_config.pool_timeout,
            'pool_recycle': db_config.pool_recycle,
            'pool_pre_ping': db_config.pool_pre_ping,
        }

        return async_engine_from_config(
            configuration,
            prefix='',
            connect_args={
                'timeout': 5,  # asyncpg timeout
            },
        )

    def get_engine(self, name: str) -> AsyncEngine:
        try:
            engine = self.engines[name]
        except KeyError:
            logger.debug('Creating engine: %s', name)
            engine = self.async_engine_from_db_config(name)
            self.engines[name] = engine

        return engine

    def get_session_maker(self, name: str):
        try:
            session_maker = self.session_makers[name]
        except KeyError:
            logger.debug('Creating session maker: %s', name)
            engine = self.get_engine(name)
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            self.session_makers[name] = session_maker

        return session_maker

    def get_async_session(self, name: str) -> Callable[[], AsyncGenerator[AsyncSession]]:
        async def get_session() -> AsyncGenerator[AsyncSession]:
            session_maker = self.get_session_maker(name)

            async with session_maker() as session:
                yield session

        return get_session

    def get_session_context(self, name: str):
        # TODO: cache
        return contextlib.asynccontextmanager(self.get_async_session(name))


async def is_healthy(engine: AsyncEngine) -> bool:
    try:
        async with engine.begin() as conn:
            result = await conn.execute('SELECT 1;')

            return result.scalar() == 1
    except Exception:
        logger.exception('Database connection is not healthy.')

        return False
