import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from typing import TYPE_CHECKING

try:
    from sqlalchemy import MetaData, text
    from sqlalchemy.exc import OperationalError, SQLAlchemyError
    from sqlalchemy.exc import TimeoutError as SATimeoutError
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

# How long (seconds) to wait for a connection from the pool before giving up.
# This is the main guard against silent hangs.
_DEFAULT_POOL_ACQUIRE_TIMEOUT = 10
# Timeout passed to asyncpg for the TCP connect itself.
_DEFAULT_CONNECT_TIMEOUT = 5
# Timeout for the health-check query.
_DEFAULT_HEALTH_CHECK_TIMEOUT = 5


class DatabaseError(Exception):
    """Base class for session-manager errors."""


class EngineCreationError(DatabaseError):
    """Raised when an engine cannot be created."""


class SessionAcquireError(DatabaseError):
    """Raised when a session cannot be acquired within the allotted time."""


class AsyncSessionManager:
    def __init__(
        self,
        db_configs: Mapping[str, DBSettings],
        pool_acquire_timeout: float = _DEFAULT_POOL_ACQUIRE_TIMEOUT,
    ) -> None:
        self.db_configs: Mapping[str, DBSettings] = db_configs
        self.pool_acquire_timeout = pool_acquire_timeout
        self.engines: dict[str, AsyncEngine] = {}
        self.session_makers: dict[str, async_sessionmaker] = {}

    def get_db_config(self, name: str) -> DBSettings:
        try:
            return self.db_configs[name]
        except KeyError:
            logger.exception('Missing database config for key %r. Available: %s', name, list(self.db_configs))
            raise

    def _build_engine(self, name: str) -> AsyncEngine:
        db_config = self.get_db_config(name)
        dsn = db_config.dsn
        str_dsn = str(dsn)

        logger.debug('Building engine for %r (dsn=%s)', name, dsn)

        configuration = {
            'url': str_dsn,
            'echo': db_config.echo,
            'pool_size': db_config.pool_size,
            'max_overflow': db_config.max_overflow,
            # pool_timeout: seconds to wait for a conn from the pool.
            # Falls back to default so the pool never blocks forever.
            'pool_timeout': getattr(db_config, 'pool_timeout', _DEFAULT_POOL_ACQUIRE_TIMEOUT),
            'pool_recycle': db_config.pool_recycle,
            'pool_pre_ping': db_config.pool_pre_ping,
        }
        connect_args = {'timeout': _DEFAULT_CONNECT_TIMEOUT}
        # For asyncpg, command_timeout provides a per-statement timeout.
        if 'postgresql' in str_dsn:
            connect_args['command_timeout'] = float(db_config.statement_timeout)

        try:
            engine = async_engine_from_config(
                configuration,
                prefix='',
                connect_args=connect_args,
            )
        except Exception as e:
            logger.exception('Failed to create engine for %r', name)

            msg = f'Could not create engine for {name!r}'
            raise EngineCreationError(msg) from e

        logger.info(
            'Engine created for %r (pool_size=%s, max_overflow=%s, pool_timeout=%s)',
            name,
            configuration['pool_size'],
            configuration['max_overflow'],
            configuration['pool_timeout'],
        )

        return engine

    def get_engine(self, name: str) -> AsyncEngine:
        try:
            return self.engines[name]
        except KeyError:
            engine = self._build_engine(name)
            self.engines[name] = engine

            return engine

    async def dispose_engine(self, name: str) -> None:
        """Gracefully close all pooled connections for *name*."""
        engine = self.engines.pop(name, None)
        self.session_makers.pop(name, None)

        if engine is not None:
            logger.info('Disposing engine for %r', name)

            await engine.dispose()

    async def dispose_all(self) -> None:
        """Gracefully close every managed engine."""
        for name in list(self.engines):
            await self.dispose_engine(name)

    def get_session_maker(self, name: str) -> async_sessionmaker:
        try:
            return self.session_makers[name]
        except KeyError:
            logger.debug('Creating session maker for %r', name)
            engine = self.get_engine(name)
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            self.session_makers[name] = session_maker

            return session_maker

    def get_async_session(self, name: str) -> Callable[[], AsyncGenerator[AsyncSession]]:
        """
        Return a dependency-injection-friendly async generator that yields a
        session, wrapped in an outer timeout so it can never hang silently.

        Usage (FastAPI / any DI framework):
            session: AsyncSession = Depends(manager.get_async_session("default"))
        """

        async def get_session() -> AsyncGenerator[AsyncSession]:
            session_acquired = False
            session_maker = self.get_session_maker(name)
            t0 = time.monotonic()
            logger.debug('Acquiring session for %r', name)

            try:
                async with asyncio.timeout(self.pool_acquire_timeout):
                    async with session_maker() as session:
                        session_acquired = True
                        elapsed = time.monotonic() - t0
                        logger.debug('Session acquired for %r in %.3fs', name, elapsed)

                        try:
                            yield session
                        except Exception:
                            logger.exception('Database communication error for %r; rolling back', name)
                            await session.rollback()

                            raise
            except TimeoutError as e:
                if session_acquired:
                    raise

                elapsed = time.monotonic() - t0
                logger.exception(
                    'Timed out waiting for a session for %r after %.3fs (limit=%.1fs)',
                    name,
                    elapsed,
                    self.pool_acquire_timeout,
                )

                msg = f'Could not acquire a session for {name!r} within {self.pool_acquire_timeout}s'
                raise SessionAcquireError(msg) from e
            except (OperationalError, SATimeoutError) as e:
                if session_acquired:
                    raise

                elapsed = time.monotonic() - t0

                logger.exception('DB error acquiring session for %r after %.3fs', name, elapsed)

                msg = f'DB error for {name!r}: {e}'
                raise SessionAcquireError(msg) from e
            except SQLAlchemyError as e:
                if session_acquired:
                    raise

                logger.exception('Unexpected SQLAlchemy error for %r', name)

                msg = f'SQLAlchemy error for {name!r}'

                raise SessionAcquireError(msg) from e

        return get_session

    def get_session_context(self, name: str):
        """
        Return an async context manager that yields a session.

        Usage:
            async with manager.get_session_context("default") as session:
                ...
        """
        return contextlib.asynccontextmanager(self.get_async_session(name))


async def is_healthy(
    engine: AsyncEngine,
    timeout: float = _DEFAULT_HEALTH_CHECK_TIMEOUT,
) -> bool:
    """
    Return True only if the engine can execute a trivial query within *timeout*
    seconds.  All failures are caught and logged; never raises.
    """
    t0 = time.monotonic()

    try:
        async with asyncio.timeout(timeout):
            async with engine.begin() as conn:
                result = await conn.execute(text('SELECT 1'))
                healthy = result.scalar() == 1

        elapsed = time.monotonic() - t0

        if healthy:
            logger.debug('Health check passed in %.3fs', elapsed)
        else:
            logger.warning('Health check query returned unexpected result')
    except TimeoutError:
        elapsed = time.monotonic() - t0
        logger.exception('Health check timed out after %.3fs (limit=%.1fs)', elapsed, timeout)

        return False
    except OperationalError as e:
        msg = f'Health check failed: DB not reachable: {e}'
        logger.exception(msg)

        return False
    except SQLAlchemyError:
        logger.exception('Health check failed: SQLAlchemy error')

        return False
    except Exception:
        logger.exception('Health check failed: unexpected error')

        return False

    return healthy
