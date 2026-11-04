import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator
from os import getenv
from pathlib import Path

import pytest
import pytest_asyncio
from docker.errors import DockerException
from pydantic import HttpUrl, RedisDsn
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

OSC = shutil.which('osc')

VALKEY_IMAGE = 'valkey/valkey:9.1.2-alpine'
POSTGRES_IMAGE = 'postgres:18.1-alpine'


@pytest.fixture(scope='session')
def authority_url() -> HttpUrl:
    """The real-provider test is opt-in: the one skip that REQUIRE_CONTAINERS=1 does not turn into a failure."""
    url = getenv('TEST_OIDC_AUTHORITY_URL')
    if not url:
        pytest.skip('TEST_OIDC_AUTHORITY_URL environment variable is not set')
    return HttpUrl(url)


@pytest.fixture(scope='session')
def client_id() -> str:
    return getenv('TEST_OIDC_CLIENT_ID', '')


@pytest.fixture(scope='session')
def client_secret() -> str:
    return getenv('TEST_OIDC_CLIENT_SECRET', '')


@pytest.fixture(scope='session')
def oidc_username() -> str:
    return getenv('TEST_OIDC_USERNAME', '')


@pytest.fixture(scope='session')
def oidc_password() -> str:
    return getenv('TEST_OIDC_PASSWORD', '')


def run_osc(*args: str, stdin: Path | None = None, stdout: Path | None = None) -> None:
    """Run `osc args < stdin > stdout` with a 0600 output file (the keyring must not be accessible to others)."""
    assert OSC is not None

    out = os.open(stdout, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600) if stdout else subprocess.DEVNULL
    source = os.open(stdin or os.devnull, os.O_RDONLY)

    try:
        subprocess.run([OSC, *args], stdin=source, stdout=out, check=True, timeout=60)  # noqa: S603
    finally:
        os.close(source)

        if stdout:
            os.close(out)


@pytest.fixture
def keyring(tmp_path: Path) -> Path:
    """A fresh keyring made by the `osc` CLI; tests using it are skipped (the one allowed skip) without `osc`."""
    if OSC is None:
        pytest.skip('the osc CLI is not on PATH')

    path = tmp_path / 'keyring'
    run_osc('keyring', 'new', stdout=path)

    return path


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    integration_dir = Path(__file__).parent

    for item in items:
        if integration_dir in item.path.parents:
            item.add_marker(pytest.mark.integration)


def _use_podman_socket_by_default() -> None:
    runtime_dir = getenv('XDG_RUNTIME_DIR')

    if runtime_dir and 'DOCKER_HOST' not in os.environ and (sock := Path(runtime_dir, 'podman/podman.sock')).exists():
        os.environ['DOCKER_HOST'] = f'unix://{sock}'
        os.environ.setdefault('TESTCONTAINERS_RYUK_DISABLED', 'true')


@pytest.fixture(scope='session')
def _valkey_dsn() -> Iterator[RedisDsn]:
    from testcontainers.community.valkey import ValkeyContainer  # noqa: PLC0415

    _use_podman_socket_by_default()

    try:
        container = ValkeyContainer(VALKEY_IMAGE).start()
    except (DockerException, ConnectionError) as exc:
        if getenv('REQUIRE_CONTAINERS') == '1':
            raise

        pytest.skip(f'Valkey container is not available: {exc}')

    try:
        yield RedisDsn(f'redis://{container.get_container_host_ip()}:{container.get_exposed_port()}')
    finally:
        container.stop()


@pytest_asyncio.fixture
async def valkey_cache(_valkey_dsn: RedisDsn) -> AsyncIterator[None]:
    from python3_commons import cache  # noqa: PLC0415
    from python3_commons.conf import get_valkey_settings  # noqa: PLC0415

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv('VALKEY_DSN', str(_valkey_dsn))
        mp.delenv('VALKEY_SENTINEL_DSN', raising=False)
        get_valkey_settings.cache_clear()

        try:
            yield
        finally:
            await cache.close_client()
            get_valkey_settings.cache_clear()


@pytest.fixture(scope='session')
def _pg_url() -> Iterator[str]:
    from testcontainers.community.postgres import PostgresContainer  # noqa: PLC0415

    _use_podman_socket_by_default()

    try:
        container = PostgresContainer(POSTGRES_IMAGE, driver='asyncpg').start()
    except (DockerException, ConnectionError) as exc:
        if getenv('REQUIRE_CONTAINERS') == '1':
            raise

        pytest.skip(f'PostgreSQL container is not available: {exc}')

    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest_asyncio.fixture
async def pg_engine(_pg_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(_pg_url)

    try:
        yield engine
    finally:
        await engine.dispose()
