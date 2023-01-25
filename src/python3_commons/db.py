import asyncio
import logging

from asyncpg import CannotConnectNowError
from databases import Database
from pydantic import PostgresDsn

logger = logging.getLogger(__name__)


async def connect_to_db(database: Database, dsn: PostgresDsn):
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
