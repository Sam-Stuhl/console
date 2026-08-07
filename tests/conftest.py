import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from console import config, github
from console.db.models import Base
from console.db.session import get_session
from console.main import app


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Fresh in-memory schema. Yields a session factory for tests that talk
    to the database directly (engine, reaper) or through the app."""
    key_file = tmp_path / "key"
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, "KEY_FILE", str(key_file))

    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(db):
    async def override_session():
        async with db() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


GITHUB_CLIENT_ID = "Iv1.testclientid"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = "" if payload is None else str(payload)

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeGitHubClient:
    """Stands in for httpx.AsyncClient in console.github, the pattern
    test_appicon.py uses. Routes are matched by URL substring so a test only
    spells out what it cares about; an unrouted URL raises the way a real
    connection failure would."""

    routes: dict = {}
    requests: list = []

    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def _answer(self, method, url, **kwargs):
        FakeGitHubClient.requests.append((method, url, kwargs))
        for fragment, response in FakeGitHubClient.routes.items():
            if fragment in url:
                if isinstance(response, Exception):
                    raise response
                return response
        raise httpx.ConnectError(f"no route: {url}")

    async def post(self, url, **kwargs):
        return self._answer("POST", url, **kwargs)

    async def get(self, url, **kwargs):
        return self._answer("GET", url, **kwargs)


@pytest.fixture
def github_http(monkeypatch):
    """A stubbed GitHub, with a client id configured so the device flow is
    available. Set .routes to decide what it answers."""
    FakeGitHubClient.routes = {}
    FakeGitHubClient.requests = []
    monkeypatch.setattr(github.httpx, "AsyncClient", FakeGitHubClient)
    monkeypatch.setattr(config, "GITHUB_CLIENT_ID", GITHUB_CLIENT_ID)
    return FakeGitHubClient
