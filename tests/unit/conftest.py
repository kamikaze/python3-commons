from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from tests.settings_env import hermetic_settings  # noqa: F401  # autouse fixture: shared environment scrub

if TYPE_CHECKING:
    from aiohttp import ClientResponse, ClientSession, web
    from aiohttp.test_utils import TestServer
else:
    try:
        from aiohttp import ClientResponse, ClientSession, web
        from aiohttp.test_utils import TestServer
    except ImportError:  # api-client extra not installed
        ClientResponse = ClientSession = web = TestServer = None


@dataclass
class RecordedRequest:
    method: str
    path_qs: str
    headers: dict[str, str]
    body: bytes


@dataclass
class RecordingServer:
    """A real aiohttp server answering every request with ``status``/``body`` and recording what it got."""

    server: TestServer
    status: int = HTTPStatus.OK
    body: str = 'ok'
    requests: list[RecordedRequest] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return str(self.server.make_url(''))

    @property
    def last(self) -> RecordedRequest:
        return self.requests[-1]


@pytest_asyncio.fixture
async def http_server() -> AsyncIterator[RecordingServer]:
    holder: dict[str, RecordingServer] = {}

    async def handle(req: web.Request) -> web.Response:
        recording = holder['server']
        recording.requests.append(RecordedRequest(req.method, req.path_qs, dict(req.headers), await req.read()))

        return web.Response(status=recording.status, text=recording.body)

    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', handle)

    test_server = TestServer(app)

    async with test_server:
        holder['server'] = RecordingServer(test_server)

        yield holder['server']


@pytest_asyncio.fixture
async def aiohttp_session() -> AsyncIterator[ClientSession]:
    session = ClientSession()

    try:
        yield session
    finally:
        await session.close()


@pytest.fixture
def make_response() -> Callable[..., MagicMock]:
    """Build a mocked ``ClientResponse``; ``ok`` follows the status unless given."""

    def factory(
        status: int = HTTPStatus.OK,
        *,
        text: str = '',
        body: bytes | None = None,
        ok: bool | None = None,
    ) -> MagicMock:
        response = MagicMock(spec=ClientResponse)
        response.status = status
        response.ok = status < HTTPStatus.BAD_REQUEST if ok is None else ok
        response.text = AsyncMock(return_value=text)
        response.read = AsyncMock(return_value=text.encode() if body is None else body)
        response.json = AsyncMock(return_value={})

        return response

    return factory


@pytest.fixture
def make_session() -> Callable[..., MagicMock]:
    """Build a mocked ``ClientSession`` whose ``request()`` context manager yields ``response``."""

    def factory(response: MagicMock | None = None) -> MagicMock:
        context = AsyncMock()
        context.__aenter__.return_value = response
        context.__aexit__.return_value = None
        session = MagicMock(spec=ClientSession)
        session.request.return_value = context
        session.cookie_jar.filter_cookies.return_value = {}

        return session

    return factory
