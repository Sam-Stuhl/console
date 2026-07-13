"""Credential expiry: status/state computation, one-shot warning with dedup and
renewal reopening it, and the API."""

from datetime import date

import pytest

from console import credentials, settings_store

TODAY = date(2026, 1, 1)
GHCR = settings_store.GHCR_TOKEN
CF = settings_store.CF_API_TOKEN


@pytest.fixture
def sent(monkeypatch):
    records: list[dict] = []

    async def fake_send(session, title, message, **kwargs):
        records.append({"title": title, **kwargs})
        return True

    monkeypatch.setattr(credentials.alerts, "send", fake_send)
    return records


async def test_status_states(db):
    async with db() as session:
        await settings_store.set_value(session, GHCR, "ghp_x")
        await credentials.set_expiry(session, GHCR, "2026-01-10")  # 9 days -> warn
        await session.commit()
    async with db() as session:
        result = await credentials.status(session, today=TODAY)

    ghcr = next(c for c in result if c["key"] == GHCR)
    assert ghcr["set"] is True
    assert ghcr["expires_at"] == "2026-01-10"
    assert ghcr["days_left"] == 9
    assert ghcr["state"] == "warn"

    cf = next(c for c in result if c["key"] == CF)
    assert cf["set"] is False
    assert cf["state"] == "none"


async def test_status_ok_and_expired(db):
    async with db() as session:
        await settings_store.set_value(session, GHCR, "x")
        await credentials.set_expiry(session, GHCR, "2026-06-01")  # far out -> ok
        await settings_store.set_value(session, CF, "y")
        await credentials.set_expiry(session, CF, "2025-12-25")  # past -> expired
        await session.commit()
    async with db() as session:
        result = {c["key"]: c for c in await credentials.status(session, today=TODAY)}
    assert result[GHCR]["state"] == "ok"
    assert result[CF]["state"] == "expired"
    assert result[CF]["days_left"] == -7


async def test_check_alerts_once_then_dedupes(db, sent):
    async with db() as session:
        await settings_store.set_value(session, CF, "cf")
        await credentials.set_expiry(session, CF, "2026-01-05")  # 4 days -> warn
        await session.commit()
    async with db() as session:
        await credentials.check_expiries(session, today=TODAY)
    assert len(sent) == 1
    assert sent[0]["priority"] == "high"
    async with db() as session:
        await credentials.check_expiries(session, today=TODAY)
    assert len(sent) == 1  # deduped by date


async def test_check_ignores_unset_token(db, sent):
    async with db() as session:
        # expiry set, but the token itself is not configured
        await credentials.set_expiry(session, GHCR, "2026-01-05")
        await session.commit()
    async with db() as session:
        await credentials.check_expiries(session, today=TODAY)
    assert sent == []


async def test_renewal_reopens_alert(db, sent):
    async with db() as session:
        await settings_store.set_value(session, GHCR, "x")
        await credentials.set_expiry(session, GHCR, "2026-01-05")
        await session.commit()
    async with db() as session:
        await credentials.check_expiries(session, today=TODAY)  # alert 1
    async with db() as session:
        await credentials.set_expiry(session, GHCR, "2027-01-01")  # renewed, far out
        await session.commit()
    async with db() as session:
        await credentials.check_expiries(session, today=TODAY)  # beyond window, no alert
    assert len(sent) == 1
    async with db() as session:
        await credentials.set_expiry(session, GHCR, "2026-01-05")  # close again
        await session.commit()
    async with db() as session:
        await credentials.check_expiries(session, today=TODAY)  # re-alerts
    assert len(sent) == 2


async def test_credentials_api(client):
    listed = await client.get("/api/credentials")
    assert listed.status_code == 200
    assert len(listed.json()) == 3

    ok = await client.put(f"/api/credentials/{GHCR}/expiry", json={"expires_at": "2026-12-31"})
    assert ok.status_code == 204
    after = {c["key"]: c for c in (await client.get("/api/credentials")).json()}
    assert after[GHCR]["expires_at"] == "2026-12-31"

    bad = await client.put(f"/api/credentials/{GHCR}/expiry", json={"expires_at": "nope"})
    assert bad.status_code == 400
    unknown = await client.put("/api/credentials/whatever/expiry", json={"expires_at": "2026-12-31"})
    assert unknown.status_code == 404

    cleared = await client.put(f"/api/credentials/{GHCR}/expiry", json={"expires_at": None})
    assert cleared.status_code == 204
    after = {c["key"]: c for c in (await client.get("/api/credentials")).json()}
    assert after[GHCR]["expires_at"] is None
