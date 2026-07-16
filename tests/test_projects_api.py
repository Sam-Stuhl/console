from datetime import datetime, timedelta

from console import config
from console.db.models import Deployment

PROJECT = {
    "name": "notion-sync",
    "repo": "sam-stuhl/notion-sync",
    "subdomain": "notion-sync",
}

NOW = datetime(2026, 7, 16, 12, 0, 0)


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


async def _add_deployment(db, project_id, sha, status, minutes_ago):
    async with db() as session:
        session.add(
            Deployment(
                project_id=project_id,
                sha=sha,
                status=status,
                created_at=NOW - timedelta(minutes=minutes_ago),
            )
        )
        await session.commit()


async def test_list_status_no_deployments(client):
    await create(client)
    p = (await client.get("/api/projects")).json()[0]
    assert p["deploy_status"] is None
    assert p["is_live"] is False


async def test_list_status_latest_and_is_live(client, db):
    pid = (await create(client)).json()["id"]
    # An older live deploy, then a newer in-flight one on top of it.
    await _add_deployment(db, pid, "old", "live", minutes_ago=10)
    await _add_deployment(db, pid, "new", "deploying", minutes_ago=1)

    p = {x["id"]: x for x in (await client.get("/api/projects")).json()}[pid]
    assert p["deploy_status"] == "deploying"  # newest wins
    assert p["is_live"] is True  # a live deployment still exists

    # get_one carries the same fields
    one = (await client.get(f"/api/projects/{pid}")).json()
    assert one["deploy_status"] == "deploying" and one["is_live"] is True


async def test_list_status_failed_only(client, db):
    pid = (await create(client)).json()["id"]
    await _add_deployment(db, pid, "boom", "failed", minutes_ago=1)
    p = {x["id"]: x for x in (await client.get("/api/projects")).json()}[pid]
    assert p["deploy_status"] == "failed"
    assert p["is_live"] is False


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
