import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from console import config
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
