# ruff: noqa: SLF001
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from python3_commons.conf import DBSettings
from python3_commons.db import AsyncSessionManager, EngineCreationError, SessionAcquireError, is_healthy

ASYNCPG_DSN = 'postgresql+asyncpg://user:pass@localhost/db'


def _manager(*, dsn: str | None = ASYNCPG_DSN, **kwargs) -> AsyncSessionManager:
    kwargs.setdefault('probe_connection', False)

    return AsyncSessionManager({'default': DBSettings(dsn=dsn)}, **kwargs)


@pytest.mark.asyncio
async def test_async_session_manager_timeout(mocker, caplog):
    caplog.set_level('DEBUG')
    # Mock DBSettings
    db_config = DBSettings(dsn='postgresql+asyncpg://user:pass@localhost/db', statement_timeout=1)

    manager = AsyncSessionManager(db_configs={'default': db_config}, probe_connection=False)

    # Mock session and session maker
    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    # session_maker() returns a context manager
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    mocker.patch.object(manager, 'get_session_maker', return_value=mock_session_maker)

    # Mock a driver-level timeout (TimeoutError)
    mock_session.execute.side_effect = TimeoutError()

    # We want to verify that TimeoutError from the driver is
    # logged as "Error occurred while db session ... was open", rolled back, and re-raised.
    with pytest.raises(TimeoutError):
        async with manager.session('default') as session:
            await session.execute(text('SELECT 1'))

    assert 'Error occurred while db session' in caplog.text


@pytest.mark.asyncio
async def test_async_session_manager_engine_config(mocker):
    db_config = DBSettings(dsn='postgresql+asyncpg://user:pass@localhost/db', statement_timeout=42)
    manager = AsyncSessionManager(db_configs={'default': db_config}, probe_connection=False)

    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    # Check that command_timeout was passed to connect_args for asyncpg
    _, kwargs = mock_engine_from_config.call_args
    assert kwargs['connect_args']['command_timeout'] == 42.0


@pytest.mark.asyncio
async def test_async_session_manager_logging(mocker, caplog):
    caplog.set_level('DEBUG')
    # Mock DBSettings
    db_config = DBSettings(dsn='postgresql+asyncpg://user:pass@localhost/db', statement_timeout=1)

    manager = AsyncSessionManager(db_configs={'default': db_config}, probe_connection=False)

    # Mock session and session maker
    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    mocker.patch.object(manager, 'get_session_maker', return_value=mock_session_maker)

    # Mock a session method to fail
    mock_session.execute.side_effect = Exception('DB Error')

    with pytest.raises(Exception, match='DB Error'):
        async with manager.session('default') as session:
            await session.execute(text('SELECT 1'))

    assert 'Error occurred while db session' in caplog.text


@pytest.mark.asyncio
async def test_aclose_with_one_failing_engine_disposes_the_rest(caplog):
    manager = _manager()
    good, bad = MagicMock(dispose=AsyncMock()), MagicMock(dispose=AsyncMock(side_effect=RuntimeError('boom')))
    manager._engines = {'a': bad, 'b': good}
    manager._session_makers = {'a': MagicMock()}

    await manager.aclose()

    good.dispose.assert_awaited_once()
    bad.dispose.assert_awaited_once()
    assert manager._engines == {}
    assert manager._session_makers == {}
    assert "Failed to dispose engine for 'a'" in caplog.text


@pytest.mark.asyncio
async def test_async_context_manager_returns_self_and_closes():
    manager = _manager()
    engine = MagicMock(dispose=AsyncMock())

    async with manager as entered:
        assert entered is manager
        manager._engines['default'] = engine

    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispose_all_keeps_lazy_rebuild():
    manager = _manager()
    engine = MagicMock(dispose=AsyncMock())
    manager._engines['default'] = engine

    await manager.dispose_all()

    engine.dispose.assert_awaited_once()
    assert manager.get_engine('default') is not engine
    await manager.aclose()


def test_get_async_session_is_memoised_per_name():
    manager = _manager()

    assert manager.get_async_session('default') is manager.get_async_session('default')
    assert manager.get_async_session('default') is not manager.get_async_session('other')


def test_psycopg_dsn_gets_no_asyncpg_connect_args(mocker):
    manager = _manager(dsn='postgresql+psycopg://user:pass@localhost/db')
    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    assert mock_engine_from_config.call_args.kwargs['connect_args'] == {}


def test_asyncpg_dsn_gets_connect_args(mocker):
    manager = _manager()
    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    assert mock_engine_from_config.call_args.kwargs['connect_args'] == {'timeout': 5, 'command_timeout': 30.0}


def test_pool_timeout_above_acquire_timeout_logs_warning_and_caps(mocker, caplog):
    manager = AsyncSessionManager(
        {'default': DBSettings(dsn=ASYNCPG_DSN, pool_timeout=30)}, pool_acquire_timeout=2, probe_connection=False
    )
    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    assert mock_engine_from_config.call_args.args[0]['pool_timeout'] == 2
    warnings_ = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings_) == 1
    assert 'pool_timeout=30' in warnings_[0].getMessage()


def test_pool_timeout_within_acquire_timeout_does_not_warn(mocker, caplog):
    manager = AsyncSessionManager(
        {'default': DBSettings(dsn=ASYNCPG_DSN, pool_timeout=1)}, pool_acquire_timeout=2, probe_connection=False
    )
    mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_default_pool_timeout_is_capped_without_warning(mocker, caplog, monkeypatch):
    monkeypatch.delenv('DB_POOL_TIMEOUT', raising=False)
    manager = AsyncSessionManager({'default': DBSettings(dsn=ASYNCPG_DSN)}, probe_connection=False)
    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    # The default DB_POOL_TIMEOUT (30) is capped at the default acquire timeout (10) without a warning
    assert mock_engine_from_config.call_args.args[0]['pool_timeout'] == manager.pool_acquire_timeout
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_pool_timeout_from_environment_above_acquire_timeout_warns(mocker, caplog, monkeypatch):
    monkeypatch.setenv('DB_POOL_TIMEOUT', '45')
    manager = AsyncSessionManager({'default': DBSettings(dsn=ASYNCPG_DSN)}, probe_connection=False)
    mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    assert [r for r in caplog.records if r.levelno == logging.WARNING and 'pool_timeout=45' in r.getMessage()]


def test_missing_dsn_raises_before_engine_creation(mocker):
    manager = _manager(dsn=None)
    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    with pytest.raises(EngineCreationError, match="DSN not configured for 'default'"):
        manager.get_engine('default')

    mock_engine_from_config.assert_not_called()


def test_probe_connection_none_warns_once_at_construction():
    with pytest.warns(DeprecationWarning, match='probe_connection=True') as record:
        AsyncSessionManager({})

    assert len(record) == 1
    assert record[0].filename == __file__


@pytest.mark.parametrize('probe', [True, False])
def test_explicit_probe_connection_does_not_warn(probe):
    AsyncSessionManager({}, probe_connection=probe)


@pytest.mark.asyncio
@pytest.mark.parametrize(('probe', 'expected'), [(True, 1), (False, 0)])
async def test_probe_connection_controls_pre_acquire_probe(mocker, probe, expected):
    manager = _manager(probe_connection=probe)
    session = AsyncMock()
    maker = MagicMock()
    maker.return_value.__aenter__.return_value = session
    mocker.patch.object(manager, 'get_session_maker', return_value=maker)

    async with manager.session('default'):
        pass

    assert session.connection.await_count == expected


@pytest.mark.asyncio
async def test_probe_failure_raises_session_acquire_error_without_error_log(mocker, caplog):
    manager = _manager(probe_connection=True)
    session = AsyncMock()
    session.connection.side_effect = OperationalError('SELECT 1', {}, Exception('down'))
    maker = MagicMock()
    maker.return_value.__aenter__.return_value = session
    mocker.patch.object(manager, 'get_session_maker', return_value=maker)

    with pytest.raises(SessionAcquireError) as exc_info:
        async with manager.session('default'):
            pass

    assert exc_info.value.__notes__
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_deprecated_attribute_names_warn_on_read_and_reject_assignment():
    manager = _manager()

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert manager.engines is manager._engines

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert manager.session_makers is manager._session_makers

    with pytest.raises(AttributeError):
        manager.engines = {}  # ty: ignore[invalid-assignment]  # pyright: ignore[reportAttributeAccessIssue]


def test_get_session_context_is_deprecated():
    manager = _manager()

    with pytest.warns(DeprecationWarning, match='1.0'):
        factory = manager.get_session_context('default')

    assert callable(factory)


def test_unknown_name_raises_key_error_without_error_record(caplog):
    manager = _manager()

    with pytest.raises(KeyError) as exc_info:
        manager.get_engine('nope')

    assert exc_info.value.__notes__
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_failed_engine_build_has_no_key_error_context(mocker, caplog):
    manager = _manager()
    mocker.patch('python3_commons.db.async_engine_from_config', side_effect=ValueError('bad'))

    with pytest.raises(EngineCreationError) as exc_info:
        manager.get_engine('default')

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert exc_info.value.__cause__.__context__ is None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_is_healthy_failure_logs_one_warning_without_traceback(caplog):
    engine = MagicMock()
    engine.begin.side_effect = RuntimeError('down')

    assert await is_healthy(engine) is False

    assert [(r.levelno, r.exc_info) for r in caplog.records] == [(logging.WARNING, None)]
    assert 'Health check failed' in caplog.text
