from pathlib import Path

import yaml

from console import config
from console.schema.console_toml import parse_console_toml

PROJECT = {
    "name": "fotoshare",
    "repo": "example-owner/fotoshare",
    "subdomain": "fotos",
}

REPO_ROOT = Path(__file__).resolve().parents[1]
REUSABLE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "app-deploy.yml"


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


async def test_caller_workflow_is_valid_and_wired(client):
    project_id = (
        await client.post(
            "/api/projects",
            json={**PROJECT, "name": "photos", "subdomain": "photos", "branch": "release"},
        )
    ).json()["id"]
    workflow = (await client.get(f"/api/projects/{project_id}/starters")).json()[
        "workflow"
    ]

    parsed = yaml.safe_load(workflow)
    # yaml parses "on:" as the boolean True key
    assert parsed[True] == {"push": {"branches": ["release"]}}  # prefilled branch
    job = parsed["jobs"]["deploy"]
    assert (
        job["uses"]
        == f"{config.WORKFLOW_REPO}/.github/workflows/app-deploy.yml@main"
    )
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["packages"] == "write"


def test_reusable_workflow_is_valid_and_authenticates_by_oidc():
    parsed = yaml.safe_load(REUSABLE_WORKFLOW.read_text())
    assert parsed[True] == {"workflow_call": None}  # only callable, not self-triggered

    job = parsed["jobs"]["build-and-deploy"]
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["packages"] == "write"

    body = REUSABLE_WORKFLOW.read_text()
    assert "audience=console" in body  # the console's expected audience
    assert "/hooks/build-started" in body
    assert "/hooks/build-finished" in body
    # both outcomes are reported so a broken build fails fast, not via reaper
    assert "if: success()" in body
    assert "failure()" in body
    # commit message is bound to env, never inlined into a run script
    assert "COMMIT_MSG: ${{ github.event.head_commit.message }}" in body


def _notify_steps() -> list[dict]:
    steps = yaml.safe_load(REUSABLE_WORKFLOW.read_text())["jobs"][
        "build-and-deploy"
    ]["steps"]
    return [s for s in steps if "/hooks/" in s.get("run", "")]


def test_every_notify_mints_its_own_token():
    """A GitHub OIDC token lives for minutes. One minted at the top of the
    job and reused after the build expires on any build slower than that,
    the console rejects the notify, and the deployment sits in "building"
    with no image while the run shows green."""
    notifies = _notify_steps()
    assert len(notifies) == 3  # started, finished-success, finished-failure
    for step in notifies:
        assert "mint_console_token" in step["run"], step["name"]

    # No step may stash a token for a later step to pick up.
    for step in yaml.safe_load(REUSABLE_WORKFLOW.read_text())["jobs"][
        "build-and-deploy"
    ]["steps"]:
        run = step.get("run", "")
        assert "CONSOLE_TOKEN" not in run or "GITHUB_ENV" not in run, step.get("name")


def test_a_refused_build_finished_fails_the_run():
    """curl exits 0 on an HTTP 401, so without --fail-with-body a rejected
    notify leaves a green run and a console that was never told."""
    for step in _notify_steps():
        if "/hooks/build-finished" in step["run"]:
            assert "--fail-with-body" in step["run"], step["name"]
