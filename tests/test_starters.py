from console.schema.console_toml import parse_console_toml

PROJECT = {
    "name": "fotoshare",
    "repo": "sam-stuhl/fotoshare",
    "subdomain": "fotos",
}


async def test_starters_are_prefilled_and_valid(client):
    project_id = (await client.post("/api/projects", json=PROJECT)).json()["id"]
    response = await client.get(f"/api/projects/{project_id}/starters")
    assert response.status_code == 200
    body = response.json()

    # The console.toml starter must pass the exact validator deploys use
    cfg, warnings = parse_console_toml(body["console_toml"])
    assert warnings == []
    assert cfg.app.name == "fotoshare"
    assert cfg.app.subdomain == "fotos"
    assert cfg.secrets == []

    # secrets line must sit above the first [section] header
    toml = body["console_toml"]
    assert toml.index("secrets = []") < toml.index("[app]")

    dockerfile = body["dockerfile"]
    assert "Dockerfile for fotoshare" in dockerfile
    assert "push to main" in dockerfile
    assert "FROM" in dockerfile and "CMD" in dockerfile


async def test_starters_unknown_project_is_404(client):
    assert (await client.get("/api/projects/nope/starters")).status_code == 404
