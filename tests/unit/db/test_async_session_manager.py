from unittest.mock import AsyncMock, MagicMock

import pytest

from python3_commons.conf import DBSettings
from python3_commons.db import AsyncSessionManager


@pytest.mark.asyncio
async def test_async_session_manager_timeout(mocker, caplog):
    # Mock DBSettings
    db_config = DBSettings(dsn='postgresql+asyncpg://user:pass@localhost/db', statement_timeout=1)

    manager = AsyncSessionManager(db_configs={'default': db_config})

    # Mock session and session maker
    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    # session_maker() returns a context manager
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    mocker.patch.object(manager, 'get_session_maker', return_value=mock_session_maker)

    # Mock a driver-level timeout (TimeoutError)
    mock_session.execute.side_effect = TimeoutError()

    get_session_ctx_func = manager.get_session_context('default')

    # We want to verify that TimeoutError from the driver is caught,
    # logged as "Error occurred while db session ... was open", rolled back, and re-raised.
    with pytest.raises(TimeoutError):
        async with get_session_ctx_func() as session:
            await session.execute('SELECT 1')

    assert 'Error occurred while db session' in caplog.text


@pytest.mark.asyncio
async def test_async_session_manager_engine_config(mocker):
    db_config = DBSettings(dsn='postgresql+asyncpg://user:pass@localhost/db', statement_timeout=42)
    manager = AsyncSessionManager(db_configs={'default': db_config})

    mock_engine_from_config = mocker.patch('python3_commons.db.async_engine_from_config')

    manager.get_engine('default')

    # Check that command_timeout was passed to connect_args for asyncpg
    _, kwargs = mock_engine_from_config.call_args
    assert kwargs['connect_args']['command_timeout'] == 42.0


@pytest.mark.asyncio
async def test_async_session_manager_logging(mocker, caplog):
    # Mock DBSettings
    db_config = DBSettings(dsn='postgresql+asyncpg://user:pass@localhost/db', statement_timeout=1)

    manager = AsyncSessionManager(db_configs={'default': db_config})

    # Mock session and session maker
    mock_session = AsyncMock()
    mock_session_maker = MagicMock()
    mock_session_maker.return_value.__aenter__.return_value = mock_session

    mocker.patch.object(manager, 'get_session_maker', return_value=mock_session_maker)

    # Mock a session method to fail
    mock_session.execute.side_effect = Exception('DB Error')

    get_session_ctx_func = manager.get_session_context('default')

    with pytest.raises(Exception, match='DB Error'):
        async with get_session_ctx_func() as session:
            await session.execute('SELECT 1')

    assert 'Error occurred while db session' in caplog.text
