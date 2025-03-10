import asyncio
import contextlib
import logging
from typing import AsyncGenerator

from asyncpg import CannotConnectNowError
from pydantic import PostgresDsn
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.asyncio.session import async_sessionmaker
from sqlalchemy.orm import declarative_base

from python3_commons.conf import db_settings

logger = logging.getLogger(__name__)

metadata = MetaData()
Base = declarative_base(metadata=metadata)
engine = create_async_engine(
    str(db_settings.db_dsn),
    # echo=True,
    pool_size=20,
    max_overflow=0,
    pool_timeout=30,
    pool_recycle=1800,  # 30 minutes
)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


get_async_session_context = contextlib.asynccontextmanager(get_async_session)


async def is_healthy(pg) -> bool:
    return await pg.fetchval('SELECT 1 FROM alembic_version;') == 1


async def connect_to_db(database, dsn: PostgresDsn):
    logger.info('Waiting for services')
    logger.debug(f'DB_DSN: {dsn}')
    timeout = 0.001
    total_timeout = 0

    for i in range(15):
        try:
            await database.connect()
        except (ConnectionRefusedError, CannotConnectNowError):
            timeout *= 2
            await asyncio.sleep(timeout)
            total_timeout += timeout
        else:
            break
    else:
        msg = f'Unable to connect database for {int(total_timeout)}s'
        logger.error(msg)
        raise ConnectionRefusedError(msg)
