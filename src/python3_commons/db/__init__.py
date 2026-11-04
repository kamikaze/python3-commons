# pyright: strict
import asyncio
import contextlib
import logging
import time
import warnings
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Self

from python3_commons._extras import require

require('database', 'sqlalchemy')

from sqlalchemy import MetaData, make_url, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine, AsyncSession, async_engine_from_config
from sqlalchemy.ext.asyncio.session import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from python3_commons.conf import DBSettings

__all__ = [
    'AsyncSessionManager',
    'Base',
    'DatabaseError',
    'EngineCreationError',
    'SessionAcquireError',
    'is_healthy',
    'metadata',
]
logger = logging.getLogger(__name__)

metadata = MetaData()


class Base(AsyncAttrs, DeclarativeBase):
    """Shared declarative base; ``Base.metadata`` is the module-level ``metadata`` object."""

    metadata = metadata


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
    """
    Owns one lazily built ``AsyncEngine`` per configured database name.

    Use it as an async resource so pooled connections are closed on shutdown::

        async with AsyncSessionManager(cfgs, probe_connection=True) as db:
            app.state.db = db
            yield

    ``probe_connection`` controls the pre-acquire connection probe done by the
    dependencies from ``get_async_session``. ``None`` (the default) keeps the
    probe on and warns; the probe becomes opt-in in 1.0.
    """

    def __init__(
        self,
        db_configs: Mapping[str, DBSettings],
        pool_acquire_timeout: float = _DEFAULT_POOL_ACQUIRE_TIMEOUT,
        *,
        probe_connection: bool | None = None,
    ) -> None:
        if probe_connection is None:
            warnings.warn(
                'the connection probe becomes opt-in in 1.0; pass probe_connection=True to keep it',
                DeprecationWarning,
                stacklevel=2,
            )

        self.db_configs: Mapping[str, DBSettings] = db_configs
        self.pool_acquire_timeout = pool_acquire_timeout
        self._probe_connection: bool = probe_connection is not False
        self._engines: dict[str, AsyncEngine] = {}
        self._session_makers: dict[str, async_sessionmaker[AsyncSession]] = {}
        self._dependencies: dict[str, Callable[[], AsyncGenerator[AsyncSession]]] = {}

    @property
    def engines(self) -> dict[str, AsyncEngine]:
        warnings.warn(
            'AsyncSessionManager.engines is deprecated and will be removed in 1.0',
            DeprecationWarning,
            stacklevel=2,
        )

        return self._engines

    @property
    def session_makers(self) -> dict[str, async_sessionmaker[AsyncSession]]:
        warnings.warn(
            'AsyncSessionManager.session_makers is deprecated and will be removed in 1.0',
            DeprecationWarning,
            stacklevel=2,
        )

        return self._session_makers

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def get_db_config(self, name: str) -> DBSettings:
        try:
            return self.db_configs[name]
        except KeyError as e:
            e.add_note(f'Missing database config for key {name!r}. Available: {list(self.db_configs)}')

            raise

    def _build_engine(self, name: str) -> AsyncEngine:
        db_config = self.get_db_config(name)
        dsn = db_config.dsn

        if dsn is None or not str(dsn):
            msg = f'DSN not configured for {name!r}'

            raise EngineCreationError(msg)

        str_dsn = str(dsn)

        logger.debug('Building engine for %r (dsn=%s)', name, dsn)

        # pool_timeout: seconds SQLAlchemy waits for a connection from the pool.
        # Cap it at the manager's acquire timeout so the pool never blocks
        # longer than the deadline we advertise to callers; falls back to the
        # default so the pool never waits forever.
        configured_pool_timeout = db_config.pool_timeout or _DEFAULT_POOL_ACQUIRE_TIMEOUT
        pool_timeout = min(configured_pool_timeout, self.pool_acquire_timeout)

        # Warn only about a value someone set (DB_POOL_TIMEOUT or pool_timeout=); the default (30) is always above the
        # default acquire timeout (10) and is capped silently.
        explicitly_set = 'pool_timeout' in getattr(db_config, 'model_fields_set', ())

        if explicitly_set and configured_pool_timeout > self.pool_acquire_timeout:
            logger.warning(
                'Pool timeout for %r is capped: configured pool_timeout=%s exceeds pool_acquire_timeout=%s',
                name,
                configured_pool_timeout,
                self.pool_acquire_timeout,
            )

        configuration = {
            'url': str_dsn,
            'echo': db_config.echo,
            'pool_size': db_config.pool_size,
            'max_overflow': db_config.max_overflow,
            'pool_timeout': pool_timeout,
            'pool_recycle': db_config.pool_recycle,
            'pool_pre_ping': db_config.pool_pre_ping,
        }

        try:
            connect_args: dict[str, float] = {}

            # These keyword arguments are asyncpg-specific.
            if make_url(str_dsn).get_driver_name() == 'asyncpg':
                connect_args['timeout'] = _DEFAULT_CONNECT_TIMEOUT
                # statement_timeout is applied as asyncpg's client-side command_timeout
                # (a per-statement deadline), not as the server-side statement_timeout.
                connect_args['command_timeout'] = float(db_config.statement_timeout)

            engine = async_engine_from_config(
                configuration,
                prefix='',
                connect_args=connect_args,
            )
        except Exception as e:
            msg = f'Could not create engine for {name!r}'
            exc = EngineCreationError(msg)
            exc.add_note(f'Engine creation failed for {name!r}: {e!r}')

            raise exc from e

        logger.info(
            'Engine created for %r (pool_size=%s, max_overflow=%s, pool_timeout=%s)',
            name,
            configuration['pool_size'],
            configuration['max_overflow'],
            configuration['pool_timeout'],
        )

        return engine

    def get_engine(self, name: str) -> AsyncEngine:
        if (engine := self._engines.get(name)) is None:
            engine = self._build_engine(name)
            self._engines[name] = engine

        return engine

    async def dispose_engine(self, name: str) -> None:
        """Gracefully close all pooled connections for *name*."""
        engine = self._engines.pop(name, None)
        self._session_makers.pop(name, None)

        if engine is not None:
            logger.info('Disposing engine for %r', name)

            await engine.dispose()

    async def aclose(self) -> None:
        """
        Dispose every managed engine concurrently; a failing engine does not stop the others.

        The manager stays usable: engines are rebuilt lazily on the next use.
        """
        engines, self._engines = self._engines, {}
        self._session_makers = {}

        results = await asyncio.gather(*(engine.dispose() for engine in engines.values()), return_exceptions=True)

        for name, result in zip(engines, results, strict=True):
            if isinstance(result, BaseException):
                logger.error('Failed to dispose engine for %r: %r', name, result)
            else:
                logger.info('Disposed engine for %r', name)

    async def dispose_all(self) -> None:
        """Gracefully close every managed engine (alias of :meth:`aclose`)."""
        await self.aclose()

    def get_session_maker(self, name: str) -> async_sessionmaker[AsyncSession]:
        if (session_maker := self._session_makers.get(name)) is None:
            logger.debug('Creating session maker for %r', name)
            engine = self.get_engine(name)
            session_maker = async_sessionmaker(engine, expire_on_commit=False)
            self._session_makers[name] = session_maker

        return session_maker

    def get_async_session(self, name: str) -> Callable[[], AsyncGenerator[AsyncSession]]:
        """
        Return a dependency-injection-friendly async generator function that yields a
        session. The same callable is returned for the same *name*, so FastAPI
        shares one session per request when it is used in several dependencies.

        Usage (FastAPI / any DI framework):
            get_db = manager.get_async_session("default")
            session: AsyncSession = Depends(get_db)
        """
        if (dependency := self._dependencies.get(name)) is not None:
            return dependency

        async def get_session() -> AsyncGenerator[AsyncSession]:
            session_maker = self.get_session_maker(name)

            async with session_maker() as session:
                if self._probe_connection:
                    await self._acquire_connection(name, session)

                try:
                    yield session
                except Exception as e:
                    logger.debug('Error occurred while db session %r was open; rolling back: %r', name, e)
                    await session.rollback()

                    raise

        self._dependencies[name] = get_session

        return get_session

    def session(self, name: str = 'default') -> AbstractAsyncContextManager[AsyncSession]:
        """
        Return an async context manager that yields a session.

        Usage:
            async with manager.session("default") as session:
                ...
        """
        return contextlib.asynccontextmanager(self.get_async_session(name))()

    async def _acquire_connection(self, name: str, session: AsyncSession) -> None:
        t0 = time.monotonic()
        logger.debug('Acquiring session for %r', name)

        try:
            # SQLAlchemy establishes the connection lazily on first use.
            # Force the pool checkout + connect to happen *now*, under a
            # hard deadline, so an exhausted pool or an unreachable DB
            # fails fast here instead of hanging inside the request
            # handler for the duration of pool_timeout.
            async with asyncio.timeout(self.pool_acquire_timeout):
                await session.connection()
                # session.connection() autobegins a transaction. Roll it
                # back so the session is returned to the caller in a clean
                # state; otherwise an explicit session.begin() would fail
                # with "a transaction is already begun on this Session".
                await session.rollback()
        except TimeoutError as e:
            elapsed = time.monotonic() - t0
            msg = f'Could not acquire a session for {name!r} within {self.pool_acquire_timeout}s'
            exc = SessionAcquireError(msg)
            exc.add_note(f'Timed out waiting for a session for {name!r} after {elapsed:.3f}s')

            raise exc from e
        except (OperationalError, SATimeoutError) as e:
            elapsed = time.monotonic() - t0
            msg = f'DB error for {name!r}: {e}'
            exc = SessionAcquireError(msg)
            exc.add_note(f'DB error acquiring session for {name!r} after {elapsed:.3f}s')

            raise exc from e
        except SQLAlchemyError as e:
            msg = f'SQLAlchemy error for {name!r}'

            raise SessionAcquireError(msg) from e

        logger.debug('Session acquired for %r in %.3fs', name, time.monotonic() - t0)

    @warnings.deprecated(
        'get_session_context is deprecated and will be removed in 1.0; use `async with manager.session(name)`'
    )
    def get_session_context(self, name: str) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
        """
        Return a factory of async context managers that yield a session.

        Usage:
            async with manager.get_session_context("default")() as session:
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
        async with asyncio.timeout(timeout), engine.begin() as conn:
            result = await conn.execute(text('SELECT 1'))
            healthy = result.scalar() == 1

        elapsed = time.monotonic() - t0

        if healthy:
            logger.debug('Health check passed in %.3fs', elapsed)
        else:
            logger.warning('Health check query returned unexpected result')
    except TimeoutError:
        elapsed = time.monotonic() - t0
        logger.warning('Health check timed out after %.3fs (limit=%.1fs)', elapsed, timeout)

        return False
    except Exception as exc:  # noqa: BLE001  # documented: never raises
        logger.warning('Health check failed: %r', exc)

        return False

    return healthy
