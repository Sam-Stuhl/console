from console import config

PROJECT = {
    "name": "notion-sync",
    "repo": "sam-stuhl/notion-sync",
    "subdomain": "notion-sync",
}

DOTENV = """\
# production credentials
DATABASE_URL=postgres://u:p@h/db
export API_KEY="abc 123"
lowercase=skipped
"""


async def create_project(client):
    return (await client.post("/api/projects", json=PROJECT)).json()["id"]


async def test_import_adds_updates_and_reports(client):
    project_id = await create_project(client)
    base = f"/api/projects/{project_id}/secrets"
    await client.put(f"{base}/API_KEY", json={"value": "old"})

    response = await client.post(f"{base}/import", json={"text": DOTENV})
    assert response.status_code == 200
    assert response.json() == {
        "added": ["DATABASE_URL"],
        "updated": ["API_KEY"],
        "skipped": ['line 4: "lowercase" is not an uppercase env-var style name'],
    }

    reveal = await client.post(f"{base}/API_KEY/reveal")
    assert reveal.json()["value"] == "abc 123"
    listing = (await client.get(base)).json()
    assert [s["key"] for s in listing] == ["API_KEY", "DATABASE_URL"]


async def test_import_empty_text_imports_nothing(client):
    project_id = await create_project(client)
    response = await client.post(
        f"/api/projects/{project_id}/secrets/import", json={"text": "\n# nothing\n"}
    )
    assert response.json() == {"added": [], "updated": [], "skipped": []}


async def test_import_unknown_project_is_404(client):
    response = await client.post(
        "/api/projects/nope/secrets/import", json={"text": "A=1"}
    )
    assert response.status_code == 404


async def test_import_without_key_file_is_503(client, monkeypatch, tmp_path):
    project_id = await create_project(client)
    monkeypatch.setattr(config, "KEY_FILE", str(tmp_path / "gone"))
    response = await client.post(
        f"/api/projects/{project_id}/secrets/import", json={"text": "A=1"}
    )
    assert response.status_code == 503


async def test_export_returns_env_text_uncached(client):
    project_id = await create_project(client)
    base = f"/api/projects/{project_id}/secrets"
    await client.put(f"{base}/DATABASE_URL", json={"value": "postgres://u:p@h/db"})
    await client.put(f"{base}/GREETING", json={"value": "hello world"})

    response = await client.post(f"{base}/export")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["env"] == (
        'DATABASE_URL=postgres://u:p@h/db\nGREETING="hello world"\n'
    )


async def test_export_empty_project(client):
    project_id = await create_project(client)
    response = await client.post(f"/api/projects/{project_id}/secrets/export")
    assert response.json()["env"] == ""
