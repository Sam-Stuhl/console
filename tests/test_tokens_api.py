"""The token management endpoints the Settings UI drives. The property that
matters most here: creation is the only response that ever carries a token."""

from console import tokens


async def test_list_is_empty_to_start(client):
    res = await client.get("/api/tokens")
    assert res.status_code == 200
    assert res.json() == []


async def test_create_returns_the_token_once(client):
    res = await client.post("/api/tokens", json={"name": "laptop", "scope": "write"})
    assert res.status_code == 201
    body = res.json()
    assert body["token"].startswith(tokens.PREFIX)
    assert body["name"] == "laptop"
    assert body["scope"] == "write"
    assert body["last_used_at"] is None

    # Every later read of the same token is metadata only.
    listed = (await client.get("/api/tokens")).json()
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert listed[0]["preview"] == body["preview"]
    assert body["token"] not in str(listed)


async def test_create_defaults_to_read_scope(client):
    res = await client.post("/api/tokens", json={"name": "agent"})
    assert res.json()["scope"] == tokens.READ


async def test_create_rejects_an_unknown_scope(client):
    res = await client.post("/api/tokens", json={"name": "agent", "scope": "admin"})
    assert res.status_code == 400
    assert "scope must be one of" in res.json()["detail"]


async def test_create_rejects_a_blank_name(client):
    assert (await client.post("/api/tokens", json={"name": "   "})).status_code == 400
    assert (await client.post("/api/tokens", json={"name": ""})).status_code == 422


async def test_revoke_removes_it(client):
    token_id = (await client.post("/api/tokens", json={"name": "agent"})).json()["id"]
    assert (await client.delete(f"/api/tokens/{token_id}")).status_code == 204
    assert (await client.get("/api/tokens")).json() == []


async def test_revoke_is_404_for_an_unknown_id(client):
    assert (await client.delete("/api/tokens/nope")).status_code == 404
