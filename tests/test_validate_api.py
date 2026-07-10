VALID = """
secrets = ["DATABASE_URL"]

[app]
name = "demo"
subdomain = "app-demo"
port = 8080

[env]
LOG_LEVEL = "info"
"""

MISPLACED_SECRETS = """
[app]
name = "demo"
subdomain = "app-demo"
port = 8080

[env]
secrets = ["DATABASE_URL"]
"""


async def test_valid_toml_returns_summary(client):
    response = await client.post("/api/validate/console-toml", json={"text": VALID})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["error"] is None
    assert body["summary"]["name"] == "demo"
    assert body["summary"]["subdomain"] == "app-demo"
    assert body["summary"]["port"] == 8080
    assert body["summary"]["env_keys"] == ["LOG_LEVEL"]
    assert body["summary"]["secrets"] == ["DATABASE_URL"]


async def test_unknown_key_is_warning_not_error(client):
    response = await client.post(
        "/api/validate/console-toml", json={"text": VALID + '\n[typo]\nx = "y"\n'}
    )
    body = response.json()
    assert body["valid"] is True
    assert body["warnings"] == ['unknown top-level key "typo" ignored']


async def test_invalid_toml_returns_the_pointed_error(client):
    response = await client.post(
        "/api/validate/console-toml", json={"text": MISPLACED_SECRETS}
    )
    body = response.json()
    assert body["valid"] is False
    assert "secrets is inside [env]" in body["error"]
    assert body["summary"] is None


async def test_garbage_returns_parse_error(client):
    response = await client.post(
        "/api/validate/console-toml", json={"text": "not toml ==="}
    )
    body = response.json()
    assert body["valid"] is False
    assert "not valid TOML" in body["error"]
