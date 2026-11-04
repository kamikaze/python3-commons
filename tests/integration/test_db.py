"""PostgreSQL-backed checks of the timestamp defaults (db-1) and eager onupdate fetch (db-6)."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy import MetaData, String, Table, event, text
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import QueuePool

from python3_commons.conf import DBSettings
from python3_commons.db import AsyncSessionManager, SessionAcquireError, is_healthy
from python3_commons.db.models.common import BaseDBUUIDModel

metadata = MetaData()


class Base(DeclarativeBase):
    metadata = metadata


class Thing(BaseDBUUIDModel, Base):
    __tablename__ = 'db_timestamps_thing'

    name: Mapped[str] = mapped_column(String)


@pytest.mark.asyncio
async def test_created_at_is_correct_under_non_utc_session_timezone(pg_engine: AsyncEngine) -> None:
    riga = create_async_engine(pg_engine.url, connect_args={'server_settings': {'timezone': 'Europe/Riga'}})

    try:
        async with riga.begin() as conn:
            await conn.run_sync(metadata.drop_all)
            await conn.run_sync(metadata.create_all)
            await conn.execute(cast('Table', Thing.__table__).insert().values(uid=uuid.uuid4(), name='a'))
            skew = (await conn.execute(text('SELECT now() - created_at FROM db_timestamps_thing'))).scalar_one()

        assert abs(skew) < timedelta(seconds=30)
    finally:
        await riga.dispose()


@pytest.mark.asyncio
async def test_updated_at_readable_after_unit_of_work_update(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)

    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)

    async with session_maker() as session:
        thing = Thing(uid=uuid.uuid4(), name='a')
        session.add(thing)
        await session.commit()
        thing.name = 'b'
        await session.commit()

        assert thing.updated_at is not None
        assert abs(datetime.now(UTC) - thing.updated_at) < timedelta(seconds=30)


def _single_connection_manager(pg_engine: AsyncEngine, *, probe_connection: bool) -> AsyncSessionManager:
    dsn = pg_engine.url.render_as_string(hide_password=False)
    config = DBSettings(dsn=dsn, pool_size=1, max_overflow=0, pool_timeout=1)

    return AsyncSessionManager({'main': config}, pool_acquire_timeout=1, probe_connection=probe_connection)


@pytest.mark.asyncio
async def test_pool_exhaustion_with_probe_raises_session_acquire_error(pg_engine: AsyncEngine) -> None:
    async with _single_connection_manager(pg_engine, probe_connection=True) as manager:
        async with manager.session('main') as holder:
            assert (await holder.execute(text('SELECT 1'))).scalar_one() == 1

            with pytest.raises(SessionAcquireError):
                async with manager.session('main'):
                    pass

        async with manager.session('main') as session:
            assert (await session.execute(text('SELECT 2'))).scalar_one() == 2


@pytest.mark.asyncio
async def test_pool_exhaustion_without_probe_surfaces_on_first_use(pg_engine: AsyncEngine) -> None:
    async with (
        _single_connection_manager(pg_engine, probe_connection=False) as manager,
        manager.session('main') as holder,
    ):
        await holder.execute(text('SELECT 1'))

        async with manager.session('main') as session:
            with pytest.raises(SATimeoutError):
                await session.execute(text('SELECT 2'))


@pytest.mark.asyncio
async def test_aclose_closes_pooled_connections_and_manager_is_reusable(pg_engine: AsyncEngine) -> None:
    manager = _single_connection_manager(pg_engine, probe_connection=False)

    async with manager.session('main') as session:
        await session.execute(text('SELECT 1'))

    engine = manager.get_engine('main')

    assert cast('QueuePool', engine.pool).checkedin() == 1

    await manager.aclose()

    assert cast('QueuePool', engine.pool).checkedin() == 0

    async with manager.session('main') as session:
        assert (await session.execute(text('SELECT 3'))).scalar_one() == 3

    await manager.aclose()


@pytest.mark.asyncio
async def test_eager_defaults_readable_after_insert_without_refresh(pg_engine: AsyncEngine) -> None:
    async with pg_engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)
        await conn.run_sync(metadata.create_all)

    session_maker = async_sessionmaker(pg_engine, expire_on_commit=False)

    async with session_maker() as session:
        thing = Thing(uid=uuid.uuid4(), name='a')
        session.add(thing)
        await session.flush()

        # eager_defaults fetches the server default in the INSERT itself, so no lazy load (MissingGreenlet) happens
        assert 'created_at' in thing.__dict__
        assert abs(datetime.now(UTC) - thing.created_at) < timedelta(seconds=30)
        assert thing.updated_at is None

        await session.commit()


@pytest.mark.asyncio
async def test_pool_exhaustion_raises_within_acquire_timeout(pg_engine: AsyncEngine) -> None:
    dsn = pg_engine.url.render_as_string(hide_password=False)
    config = DBSettings(dsn=dsn, pool_size=1, max_overflow=0, pool_timeout=30)

    async with AsyncSessionManager({'main': config}, pool_acquire_timeout=0.3, probe_connection=True) as manager:
        async with manager.session('main') as holder:
            await holder.execute(text('SELECT 1'))

            started = time.monotonic()

            with pytest.raises(SessionAcquireError):
                async with manager.session('main'):
                    pass

            assert time.monotonic() - started < 2

        async with manager.session('main') as session:
            assert (await session.execute(text('SELECT 4'))).scalar_one() == 4


@pytest.mark.asyncio
async def test_is_healthy_true_against_postgresql_and_false_when_unreachable(pg_engine: AsyncEngine) -> None:
    assert await is_healthy(pg_engine) is True

    url = pg_engine.url.set(port=1)
    unreachable = create_async_engine(url, connect_args={'timeout': 1})

    try:
        assert await is_healthy(unreachable, timeout=2) is False
    finally:
        await unreachable.dispose()


@pytest.mark.asyncio
async def test_is_healthy_times_out_on_slow_query(pg_engine: AsyncEngine) -> None:
    slow = create_async_engine(pg_engine.url)

    @event.listens_for(slow.sync_engine, 'before_cursor_execute')
    def _sleep(conn: Any, cursor: Any, statement: str, *_: object) -> None:
        cursor.execute('SELECT pg_sleep(2)')

    try:
        assert await is_healthy(slow, timeout=0.2) is False
    finally:
        await slow.dispose()
