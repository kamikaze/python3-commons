"""Acquire-timeout and error mapping of the session probe, ``is_healthy`` and ``aclose`` ordering (mocked engines)."""
# ruff: noqa: SLF001

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.sql.elements import TextClause

from python3_commons.conf import DBSettings
from python3_commons.db import AsyncSessionManager, SessionAcquireError, is_healthy

DSN = 'postgresql+asyncpg://user:pass@localhost/db'


def _manager_with_session(mocker: Any, session: AsyncMock, **kwargs: Any) -> AsyncSessionManager:
    manager = AsyncSessionManager({'default': DBSettings(dsn=DSN)}, probe_connection=True, **kwargs)
    maker = MagicMock()
    maker.return_value.__aenter__.return_value = session
    mocker.patch.object(manager, 'get_session_maker', return_value=maker)

    return manager


@pytest.mark.asyncio
async def test_acquire_timeout_maps_to_session_acquire_error(mocker: Any) -> None:
    never_set = asyncio.Event()
    session = AsyncMock()

    async def hang() -> None:
        await never_set.wait()

    session.connection.side_effect = hang
    manager = _manager_with_session(mocker, session, pool_acquire_timeout=0.01)

    with pytest.raises(SessionAcquireError, match=r'within 0\.01s') as exc_info:
        async with manager.session('default'):
            pytest.fail('the body must not run when the session cannot be acquired')

    assert isinstance(exc_info.value.__cause__, TimeoutError)
    assert any('Timed out waiting' in note for note in exc_info.value.__notes__)
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error',
    [
        OperationalError('SELECT 1', {}, Exception('connection refused')),
        SATimeoutError('QueuePool limit of size 1 overflow 0 reached'),
    ],
    ids=['operational-error', 'sqlalchemy-timeout'],
)
async def test_db_errors_map_to_session_acquire_error(mocker: Any, error: SQLAlchemyError) -> None:
    session = AsyncMock()
    session.connection.side_effect = error
    manager = _manager_with_session(mocker, session)

    with pytest.raises(SessionAcquireError, match="DB error for 'default'") as exc_info:
        async with manager.session('default'):
            pytest.fail('the body must not run when the session cannot be acquired')

    assert exc_info.value.__cause__ is error
    assert any('DB error acquiring session' in note for note in exc_info.value.__notes__)


@pytest.mark.asyncio
async def test_other_sqlalchemy_error_maps_to_session_acquire_error(mocker: Any) -> None:
    session = AsyncMock()
    error = SQLAlchemyError('boom')
    session.connection.side_effect = error
    manager = _manager_with_session(mocker, session)

    with pytest.raises(SessionAcquireError, match="SQLAlchemy error for 'default'") as exc_info:
        async with manager.session('default'):
            pytest.fail('the body must not run when the session cannot be acquired')

    assert exc_info.value.__cause__ is error


@pytest.mark.asyncio
async def test_successful_probe_rolls_back_and_yields_session(mocker: Any) -> None:
    session = AsyncMock()
    manager = _manager_with_session(mocker, session)

    async with manager.session('default') as yielded:
        assert yielded is session
        session.connection.assert_awaited_once()
        session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_in_body_rolls_back_and_reraises(mocker: Any) -> None:
    session = AsyncMock()
    manager = _manager_with_session(mocker, session)

    async def use_session() -> None:
        async with manager.session('default'):
            session.rollback.reset_mock()

            raise ValueError(msg)

    msg = 'body'

    with pytest.raises(ValueError, match='body'):
        await use_session()

    session.rollback.assert_awaited_once()


def _engine_with_connection(connection: Any) -> MagicMock:
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=connection)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    return engine


@pytest.mark.asyncio
@pytest.mark.parametrize(('scalar', 'expected'), [(1, True), (0, False)])
async def test_is_healthy_result(scalar: int, expected: bool, caplog: pytest.LogCaptureFixture) -> None:  # noqa: FBT001
    caplog.set_level(logging.DEBUG)
    connection = MagicMock()
    execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=scalar)))
    connection.execute = execute

    assert await is_healthy(_engine_with_connection(connection)) is expected

    assert execute.await_args is not None
    (statement,) = execute.await_args.args
    assert isinstance(statement, TextClause)
    assert str(statement) == str(text('SELECT 1'))
    assert ('unexpected result' in caplog.text) is (not expected)


@pytest.mark.asyncio
async def test_is_healthy_timeout_returns_false_with_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    never_set = asyncio.Event()
    connection = MagicMock()

    async def hang(*_: object) -> None:
        await never_set.wait()

    connection.execute = hang

    assert await is_healthy(_engine_with_connection(connection), timeout=0.01) is False

    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert 'timed out' in caplog.text


@pytest.mark.asyncio
async def test_is_healthy_timeout_while_acquiring_connection(caplog: pytest.LogCaptureFixture) -> None:
    never_set = asyncio.Event()
    engine = MagicMock()

    async def hang(*_: object) -> None:
        await never_set.wait()

    engine.begin.return_value.__aenter__ = hang
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)

    assert await is_healthy(engine, timeout=0.01) is False
    assert 'timed out' in caplog.text


def _engine_recording(order: list[str], name: str, *, error: BaseException | None = None) -> MagicMock:
    async def dispose() -> None:
        order.append(f'start:{name}')
        await asyncio.sleep(0)
        order.append(f'end:{name}')

        if error is not None:
            raise error

    return MagicMock(dispose=dispose)


@pytest.mark.asyncio
async def test_aclose_clears_state_before_awaiting_and_disposes_concurrently() -> None:
    manager = AsyncSessionManager({}, probe_connection=False)
    order: list[str] = []
    engine_a = _engine_recording(order, 'a')
    manager._engines = {'a': engine_a, 'b': _engine_recording(order, 'b')}
    manager._session_makers = {'a': MagicMock()}
    seen_during_dispose: list[dict[str, Any]] = []
    original: Callable[[], Any] = engine_a.dispose

    async def spy() -> None:
        seen_during_dispose.append(dict(manager._engines))
        await original()

    engine_a.dispose = spy

    await manager.aclose()

    assert seen_during_dispose == [{}]
    # both disposals start before either finishes: they run concurrently, not one after another
    assert order[:2] == ['start:a', 'start:b']
    assert sorted(order[2:]) == ['end:a', 'end:b']
    assert manager._engines == {}
    assert manager._session_makers == {}


@pytest.mark.asyncio
async def test_aclose_is_idempotent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    manager = AsyncSessionManager({}, probe_connection=False)
    engine = MagicMock(dispose=AsyncMock())
    manager._engines = {'a': engine}

    await manager.aclose()
    await manager.aclose()

    engine.dispose.assert_awaited_once()
    assert caplog.text.count("Disposed engine for 'a'") == 1


@pytest.mark.asyncio
async def test_aclose_without_engines_is_a_noop() -> None:
    await AsyncSessionManager({}, probe_connection=False).aclose()


@pytest.mark.asyncio
async def test_aclose_survives_every_engine_failing(caplog: pytest.LogCaptureFixture) -> None:
    manager = AsyncSessionManager({}, probe_connection=False)
    order: list[str] = []
    manager._engines = {
        'a': _engine_recording(order, 'a', error=RuntimeError('a')),
        'b': _engine_recording(order, 'b', error=OSError('b')),
    }

    await manager.aclose()

    assert sorted(order) == ['end:a', 'end:b', 'start:a', 'start:b']
    assert "Failed to dispose engine for 'a'" in caplog.text
    assert "Failed to dispose engine for 'b'" in caplog.text
    assert manager._engines == {}


@pytest.mark.asyncio
async def test_dispose_engine_removes_engine_and_session_maker() -> None:
    manager = AsyncSessionManager({}, probe_connection=False)
    engine = MagicMock(dispose=AsyncMock())
    manager._engines = {'a': engine, 'b': MagicMock(dispose=AsyncMock())}
    manager._session_makers = {'a': MagicMock()}

    await manager.dispose_engine('a')
    await manager.dispose_engine('missing')

    engine.dispose.assert_awaited_once()
    assert list(manager._engines) == ['b']
    assert manager._session_makers == {}
