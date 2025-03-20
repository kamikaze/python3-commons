import contextlib
import logging
from typing import AsyncGenerator, Callable, Mapping

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, async_engine_from_config
from sqlalchemy.ext.asyncio.session import async_sessionmaker
from sqlalchemy.orm import declarative_base

from python3_commons.conf import DBSettings

logger = logging.getLogger(__name__)
metadata = MetaData()
Base = declarative_base(metadata=metadata)


class AsyncSessionManager:
    def __init__(self, config: Mapping[str: DBSettings]):
        self.config: Mapping[str: DBSettings] = config
        self.engines: dict[str, AsyncEngine] = {}
        self.session_makers: dict = {}

    def get_config(self, name: str) -> Mapping[str, str | int]:
        try:
            return self.config[name]
        except KeyError:
            logger.error(f'Missing database config: {name}')

            raise

    def get_engine(self, name: str) -> AsyncEngine:
        try:
            engine = self.engines[name]
        except KeyError:
            logger.debug(f'Creating engine: {name}')
            engine = async_engine_from_config(self.config[name])
            self.engines[name] = engine

        return engine

    def get_session_maker(self, name: str):
        try:
            session_maker = self.session_makers[name]
        except KeyError:
            logger.debug(f'Creating session maker: {name}')
            engine = self.get_engine(name)
            session_maker = async_sessionmaker(engine)
            self.session_makers[name] = session_maker

        return session_maker

    def get_async_session(self, name: str) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
        async def get_session() -> AsyncGenerator[AsyncSession, None]:
            async with self.get_session_maker(name) as session:
                yield session

        return get_session

    def get_session_context(self, name: str):
        return contextlib.asynccontextmanager(lambda: self.get_async_session(name)())


async def is_healthy(engine: AsyncEngine) -> bool:
    try:
        async with engine.begin() as conn:
            result = await conn.execute('SELECT 1;')

            return result.scalar() == 1
    except Exception as e:
        logger.error(f'Database connection is not healthy: {e}')

        return False
