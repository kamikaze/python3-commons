import contextlib
import logging
from typing import AsyncGenerator, Callable, Mapping

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_engine_from_config
from sqlalchemy.ext.asyncio.session import async_sessionmaker
from sqlalchemy.orm import declarative_base

from python3_commons.conf import DBSettings

logger = logging.getLogger(__name__)
metadata = MetaData()
Base = declarative_base(metadata=metadata)


class AsyncSessionManager:
    def __init__(self, db_settings: Mapping[str, DBSettings]):
        self.db_settings: Mapping[str, DBSettings] = db_settings
        self.engines: dict[str, AsyncEngine] = {}
        self.session_makers: dict = {}

    def get_db_settings(self, name: str) -> DBSettings:
        try:
            return self.db_settings[name]
        except KeyError:
            logger.error(f'Missing database settings: {name}')

            raise

    def async_engine_from_db_settings(self, name):
        db_settings = self.get_db_settings(name)
        configuration = {
            'url': str(db_settings.dsn),
            'echo': db_settings.echo,
            'pool_size': db_settings.pool_size,
            'max_overflow': db_settings.max_overflow,
            'pool_timeout': db_settings.pool_timeout,
            'pool_recycle': db_settings.pool_recycle,
        }
        engine = async_engine_from_config(configuration, prefix='')

        return engine

    def get_engine(self, name: str) -> AsyncEngine:
        try:
            engine = self.engines[name]
        except KeyError:
            logger.debug(f'Creating engine: {name}')
            engine = self.async_engine_from_db_settings(name)
            self.engines[name] = engine

        return engine

    def get_session_maker(self, name: str):
        try:
            session_maker = self.session_makers[name]
        except KeyError:
            logger.debug(f'Creating session maker: {name}')
            engine = self.get_engine(name)
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            self.session_makers[name] = session_maker

        return session_maker

    def get_async_session(self, name: str) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
        async def get_session() -> AsyncGenerator[AsyncSession, None]:
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
    except Exception as e:
        logger.error(f'Database connection is not healthy: {e}')

        return False
