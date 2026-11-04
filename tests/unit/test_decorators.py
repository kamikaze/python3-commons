import asyncio
import logging

import pytest

from python3_commons import decorators
from python3_commons.decorators import log_execution_time, retry


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    calls: list[float] = []

    async def fake_async_sleep(delay: float) -> None:
        calls.append(delay)

    monkeypatch.setattr(decorators.asyncio, 'sleep', fake_async_sleep)
    monkeypatch.setattr(decorators.time, 'sleep', calls.append)

    return calls


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_failures(sleeps: list[float], caplog: pytest.LogCaptureFixture) -> None:
    attempts = 0

    @retry(attempts=3, delay=0.1, backoff=2.0, jitter=False)
    async def flaky(x: int) -> int:
        """Doc."""
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            msg = 'boom'
            raise ConnectionError(msg)

        return x * 2

    with caplog.at_level(logging.WARNING):
        assert await flaky(4) == 8

    assert attempts == 3
    assert sleeps == [0.1, 0.2]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    assert flaky.__name__ == 'flaky'
    assert flaky.__doc__ == 'Doc.'


@pytest.mark.asyncio
async def test_retry_async_gives_up_and_reraises(sleeps: list[float]) -> None:
    attempts = 0

    @retry(attempts=3, jitter=False)
    async def always() -> None:
        nonlocal attempts
        attempts += 1
        raise TimeoutError

    with pytest.raises(TimeoutError):
        await always()

    assert attempts == 3
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_retry_does_not_retry_other_exceptions(sleeps: list[float]) -> None:
    attempts = 0

    @retry()
    async def bad() -> None:
        nonlocal attempts
        attempts += 1
        raise ValueError

    with pytest.raises(ValueError):  # noqa: PT011
        await bad()

    assert attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_on_custom_class_and_tuple(sleeps: list[float]) -> None:
    attempts = 0

    @retry(attempts=2, retry_on=InterruptedError, jitter=False)
    async def limited() -> str:
        nonlocal attempts
        attempts += 1

        if attempts == 1:
            raise InterruptedError

        return 'ok'

    assert await limited() == 'ok'
    assert len(sleeps) == 1


@pytest.mark.asyncio
async def test_retry_never_catches_cancelled_error(sleeps: list[float]) -> None:
    attempts = 0

    @retry(attempts=3, retry_on=BaseException)
    async def cancelled() -> None:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled()

    assert attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_retry_backoff_capped_by_max_delay(sleeps: list[float]) -> None:
    @retry(attempts=5, delay=1.0, backoff=10.0, max_delay=3.0, jitter=False)
    async def fail() -> None:
        raise ConnectionError

    with pytest.raises(ConnectionError):
        await fail()

    assert sleeps == [1.0, 3.0, 3.0, 3.0]


@pytest.mark.asyncio
async def test_retry_jitter_bounded(sleeps: list[float]) -> None:
    @retry(attempts=4, delay=1.0, backoff=2.0, jitter=True)
    async def fail() -> None:
        raise ConnectionError

    with pytest.raises(ConnectionError):
        await fail()

    assert len(sleeps) == 3
    assert all(0 <= s <= cap for s, cap in zip(sleeps, [1.0, 2.0, 4.0], strict=True))


def test_retry_sync(sleeps: list[float]) -> None:
    attempts = 0

    @retry(attempts=3, delay=0.5, jitter=False)
    def flaky() -> str:
        nonlocal attempts
        attempts += 1

        if attempts < 3:
            raise ConnectionError

        return 'done'

    assert flaky() == 'done'
    assert sleeps == [0.5, 1.0]
    assert flaky.__name__ == 'flaky'


def test_retry_sync_gives_up(sleeps: list[float]) -> None:
    @retry(attempts=2, jitter=False)
    def fail() -> None:
        raise ConnectionError

    with pytest.raises(ConnectionError):
        fail()

    assert len(sleeps) == 1


def test_retry_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match='attempts'):
        retry(attempts=0)


@pytest.mark.asyncio
async def test_log_execution_time_async_default_info(caplog: pytest.LogCaptureFixture) -> None:
    @log_execution_time
    async def work() -> int:
        """Doc."""
        return 1

    with caplog.at_level(logging.DEBUG):
        assert await work() == 1

    (record,) = caplog.records
    assert record.levelno == logging.INFO
    assert 'test_log_execution_time_async_default_info.<locals>.work' in record.getMessage()
    assert work.__doc__ == 'Doc.'


@pytest.mark.asyncio
async def test_log_execution_time_level_and_logs_on_error(caplog: pytest.LogCaptureFixture) -> None:
    @log_execution_time(level=logging.DEBUG)
    async def work() -> None:
        raise RuntimeError

    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError):
        await work()

    (record,) = caplog.records
    assert record.levelno == logging.DEBUG


def test_log_execution_time_sync(caplog: pytest.LogCaptureFixture) -> None:
    @log_execution_time()
    def work(x: int) -> int:
        return x + 1

    @log_execution_time
    def work2() -> int:
        return 2

    with caplog.at_level(logging.INFO):
        assert work(1) == 2
        assert work2() == 2

    assert len(caplog.records) == 2
    assert work.__name__ == 'work'
