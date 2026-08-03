import pytest
from sqlalchemy import select

from console import oidc
from console import config
from console.api.hooks import verified_claims
from console.db.models import Deployment
from console.main import app

OWNER = "example-owner"
REPO = f"{OWNER}/some-app"

CLAIMS = {
    "repository": REPO,
    "repository_owner": OWNER,
    "ref": "refs/heads/main",
}

VALID_TOML = """
[app]
name = "demo"
subdomain = "demo"
port = 80
"""

FINISHED = {
    "repo": REPO,
    "sha": "e5f6a7b0d1c2",
    "conclusion": "success",
    "image": f"ghcr.io/{OWNER}/some-app:e5f6a7b",
    "console_toml": VALID_TOML,
}


@pytest.fixture
def claims():
    """Bypass token verification with canned claims."""
    value = dict(CLAIMS)
    app.dependency_overrides[verified_claims] = lambda: value
    yield value
    app.dependency_overrides.pop(verified_claims, None)


@pytest.fixture(autouse=True)
def configured_owner(monkeypatch):
    """The image-prefix check derives from deployment config, so pin it rather
    than depending on the environment the tests happen to run in."""
    monkeypatch.setattr(config, "OIDC_OWNER", OWNER)


@pytest.fixture(autouse=True)
def enqueued(monkeypatch):
    calls = []
    monkeypatch.setattr("console.deploy.engine.enqueue", calls.append)
    return calls


async def project(client, **overrides):
    body = {
        "name": "notion-sync",
        "repo": REPO,
        "subdomain": "notion-sync",
        **overrides,
    }
    response = await client.post("/api/projects", json=body)
    assert response.status_code == 201
    return response.json()


async def started(client, **overrides):
    return await client.post(
        "/hooks/build-started",
        json={"repo": REPO, "sha": "e5f6a7b0d1c2", **overrides},
    )


async def finished(client, **overrides):
    return await client.post("/hooks/build-finished", json={**FINISHED, **overrides})


async def get_deployment(db, deployment_id):
    async with db() as session:
        return await session.get(Deployment, deployment_id)


async def test_missing_bearer_is_401(client):
    response = await client.post(
        "/hooks/build-started", json={"repo": REPO, "sha": "x"}
    )
    assert response.status_code == 401


async def test_bad_token_is_401(client, monkeypatch):
    def reject(token):
        raise oidc.OidcError("OIDC token rejected: nope")

    monkeypatch.setattr(oidc, "verify", reject)
    response = await client.post(
        "/hooks/build-started",
        json={"repo": REPO, "sha": "x"},
        headers={"Authorization": "Bearer garbage"},
    )
    assert response.status_code == 401
    assert "rejected" in response.json()["detail"]


async def test_wrong_owner_is_403(client, monkeypatch):
    def reject(token):
        raise oidc.WrongOwner("OIDC token rejected: repository_owner")

    monkeypatch.setattr(oidc, "verify", reject)
    response = await client.post(
        "/hooks/build-started",
        json={"repo": REPO, "sha": "x"},
        headers={"Authorization": "Bearer sometoken"},
    )
    assert response.status_code == 403


async def test_repo_mismatch_is_403(client, claims):
    await project(client)
    response = await started(client, repo=f"{OWNER}/other")
    assert response.status_code == 403
    assert "token was issued for" in response.json()["detail"]


async def test_unknown_project_is_404(client, claims):
    claims["repository"] = f"{OWNER}/unregistered"
    response = await started(client, repo=f"{OWNER}/unregistered")
    assert response.status_code == 404


async def test_canonical_github_case_matches_lowercase_registration(client, claims, db):
    # Project registered lowercase; GitHub sends canonical case in both the
    # token claim and the payload. Owner and repo lookups must still match.
    await project(client)  # registered as OWNER/some-app
    claims["repository"] = "Example-Owner/Some-App"
    claims["repository_owner"] = "Example-Owner"
    response = await started(client, repo="Example-Owner/Some-App")
    assert response.status_code == 201
    assert response.json()["status"] == "building"


async def test_other_branch_is_ignored(client, claims, db):
    await project(client)
    claims["ref"] = "refs/heads/feature"
    response = await started(client)
    assert response.status_code == 200
    assert "ignored" in response.json()
    async with db() as session:
        assert (await session.scalars(select(Deployment))).all() == []


async def test_build_started_creates_then_idempotent(client, claims, db):
    await project(client)
    first = await started(client, commit_message="fix the thing")
    assert first.status_code == 201
    body = first.json()
    assert body["status"] == "building"

    again = await started(client, run_url="https://github.com/run/2")
    assert again.status_code == 200
    assert again.json()["deployment_id"] == body["deployment_id"]

    row = await get_deployment(db, body["deployment_id"])
    assert row.status == "building"
    assert row.commit_message == "fix the thing"
    assert row.run_url == "https://github.com/run/2"


async def test_build_failure_marks_failed(client, claims, db):
    await project(client)
    deployment_id = (await started(client)).json()["deployment_id"]

    response = await finished(client, conclusion="failure")
    assert response.status_code == 200
    assert response.json()["status"] == "failed"

    row = await get_deployment(db, deployment_id)
    assert row.failure_reason == "build failed (conclusion: failure)"
    assert row.build_finished_at is not None
    assert row.finished_at is not None


async def test_bad_image_ref_marks_failed(client, claims, db):
    await project(client)
    deployment_id = (await started(client)).json()["deployment_id"]
    response = await finished(client, image="docker.io/evil/image:latest")
    assert response.json()["status"] == "failed"
    row = await get_deployment(db, deployment_id)
    assert f"ghcr.io/{OWNER}/" in row.failure_reason


async def test_missing_toml_marks_failed(client, claims, db):
    await project(client)
    deployment_id = (await started(client)).json()["deployment_id"]
    await finished(client, console_toml=None)
    row = await get_deployment(db, deployment_id)
    assert "no console.toml" in row.failure_reason


async def test_invalid_toml_marks_failed(client, claims, db, enqueued):
    await project(client)
    deployment_id = (await started(client)).json()["deployment_id"]
    response = await finished(client, console_toml="[app]\nname = 'demo'")
    assert response.status_code == 200
    row = await get_deployment(db, deployment_id)
    assert row.status == "failed"
    assert "console.toml invalid" in row.failure_reason
    assert enqueued == []


async def test_valid_build_queues_and_enqueues(client, claims, db, enqueued):
    await project(client)
    deployment_id = (await started(client)).json()["deployment_id"]

    response = await finished(client)
    assert response.status_code == 202
    assert response.json() == {"deployment_id": deployment_id, "status": "queued"}
    assert enqueued == [deployment_id]

    row = await get_deployment(db, deployment_id)
    assert row.image == FINISHED["image"]
    assert '"name":"demo"' in row.config_snapshot
    assert row.build_finished_at is not None
    assert row.finished_at is None


async def test_missed_build_started_still_deploys(client, claims, enqueued):
    await project(client)
    response = await finished(client)
    assert response.status_code == 202
    assert len(enqueued) == 1


async def test_newer_queued_supersedes_older(client, claims, db):
    await project(client)
    old_id = (await finished(client, sha="aaaaaaa11111")).json()["deployment_id"]
    new_id = (await finished(client, sha="bbbbbbb22222")).json()["deployment_id"]

    old_row = await get_deployment(db, old_id)
    new_row = await get_deployment(db, new_id)
    assert old_row.status == "superseded"
    assert old_row.finished_at is not None
    assert new_row.status == "queued"


async def test_duplicate_finished_after_queue_is_noop(client, claims, db, enqueued):
    await project(client)
    deployment_id = (await finished(client)).json()["deployment_id"]

    again = await finished(client)
    assert again.status_code == 200
    assert again.json()["status"] == "queued"
    assert enqueued == [deployment_id]  # not enqueued twice
