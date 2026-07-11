import pytest

from console import cloudflare, config, settings_store


async def test_settings_lifecycle(client):
    assert (await client.get("/api/settings")).json() == {"set": []}

    # unknown key rejected
    assert (await client.put("/api/settings/nope", json={"value": "x"})).status_code == 404
    # empty value rejected
    assert (
        await client.put("/api/settings/ghcr_token", json={"value": "  "})
    ).status_code == 400

    # set, then it shows as configured (value never returned)
    assert (
        await client.put("/api/settings/ghcr_token", json={"value": "ghp_abc"})
    ).status_code == 204
    assert (await client.get("/api/settings")).json() == {"set": ["ghcr_token"]}

    # clear it
    assert (await client.delete("/api/settings/ghcr_token")).status_code == 204
    assert (await client.get("/api/settings")).json() == {"set": []}


async def test_settings_need_key_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "KEY_FILE", str(tmp_path / "gone"))
    r = await client.put("/api/settings/ghcr_token", json={"value": "x"})
    assert r.status_code == 503
    assert "console key not configured" in r.json()["detail"]


async def test_cf_credentials_from_settings(db):
    async with db() as session:
        await settings_store.set_value(session, settings_store.CF_API_TOKEN, "tok")
        await settings_store.set_value(session, settings_store.CF_ACCOUNT_ID, "acct")
        await session.commit()
    async with db() as session:
        assert await cloudflare.resolve_credentials(session) == ("tok", "acct")


async def test_cf_credentials_unconfigured_raises(db, monkeypatch):
    monkeypatch.setattr(config, "CF_ACCOUNT_ID", "")
    monkeypatch.setattr(config, "CF_API_TOKEN_FILE", "/nonexistent/token")
    async with db() as session:
        with pytest.raises(cloudflare.AccessNotConfigured):
            await cloudflare.resolve_credentials(session)
