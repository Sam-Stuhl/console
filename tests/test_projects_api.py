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
async def client(tmp_path, monkeypatch):
    key_file = tmp_path / "key"
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(config, "KEY_FILE", str(key_file))

    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


PROJECT = {
    "name": "notion-sync",
    "repo": "sam-stuhl/notion-sync",
    "subdomain": "notion-sync",
}


async def create(client, **overrides):
    return await client.post("/api/projects", json={**PROJECT, **overrides})


async def test_create_and_list(client):
    response = await create(client)
    assert response.status_code == 201
    body = response.json()
    assert body["branch"] == "main"
    assert body["id"]

    listing = await client.get("/api/projects")
    assert [p["name"] for p in listing.json()] == ["notion-sync"]


async def test_subdomain_console_rejected(client):
    response = await create(client, subdomain="console")
    assert response.status_code == 400
    assert "reserved" in response.json()["detail"]


async def test_bad_subdomain_and_repo_rejected(client):
    assert (await create(client, subdomain="Not-Valid")).status_code == 400
    assert (await create(client, repo="no-slash")).status_code == 400


async def test_duplicates_conflict(client):
    await create(client)
    for overrides in (
        {},
        {"name": "other", "repo": "sam-stuhl/other"},  # same subdomain
        {"name": "other", "subdomain": "other"},  # same repo
    ):
        response = await create(client, **overrides)
        assert response.status_code == 409, overrides
        assert "already exists" in response.json()["detail"]


async def test_get_and_delete(client):
    project_id = (await create(client)).json()["id"]
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 200
    assert (await client.delete(f"/api/projects/{project_id}")).status_code == 204
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 404
    assert (await client.get("/api/projects/nope")).status_code == 404


async def test_secret_lifecycle(client):
    project_id = (await create(client)).json()["id"]
    base = f"/api/projects/{project_id}/secrets"

    put = await client.put(f"{base}/DATABASE_URL", json={"value": "postgres://x"})
    assert put.status_code == 204

    listing = (await client.get(base)).json()
    assert [s["key"] for s in listing] == ["DATABASE_URL"]
    assert "value" not in listing[0]

    reveal = await client.post(f"{base}/DATABASE_URL/reveal")
    assert reveal.status_code == 200
    assert reveal.json()["value"] == "postgres://x"
    assert reveal.headers["cache-control"] == "no-store"

    await client.put(f"{base}/DATABASE_URL", json={"value": "postgres://y"})
    assert (await client.post(f"{base}/DATABASE_URL/reveal")).json()["value"] == "postgres://y"

    assert (await client.delete(f"{base}/DATABASE_URL")).status_code == 204
    assert (await client.post(f"{base}/DATABASE_URL/reveal")).status_code == 404


async def test_secret_key_format_enforced(client):
    project_id = (await create(client)).json()["id"]
    response = await client.put(
        f"/api/projects/{project_id}/secrets/lowercase", json={"value": "x"}
    )
    assert response.status_code == 400


async def test_secrets_cascade_on_project_delete(client):
    project_id = (await create(client)).json()["id"]
    await client.put(
        f"/api/projects/{project_id}/secrets/DATABASE_URL", json={"value": "x"}
    )
    await client.delete(f"/api/projects/{project_id}")

    second = (await create(client)).json()["id"]
    listing = await client.get(f"/api/projects/{second}/secrets")
    assert listing.json() == []


async def test_missing_key_file_gives_503_only_for_secrets(client, monkeypatch, tmp_path):
    project_id = (await create(client)).json()["id"]
    monkeypatch.setattr(config, "KEY_FILE", str(tmp_path / "gone"))

    response = await client.put(
        f"/api/projects/{project_id}/secrets/DATABASE_URL", json={"value": "x"}
    )
    assert response.status_code == 503
    assert "console key not configured" in response.json()["detail"]

    # Non-secret routes still work without a key
    assert (await client.get("/api/projects")).status_code == 200
