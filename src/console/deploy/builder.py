"""Building an app image on the box, for a deployment waiting in "building".

This is what GitHub Actions used to do: check the repo out at a sha, build
the Dockerfile, push the image to GHCR, and hand the console the tag. It
runs here now because Actions stopped starting jobs on the account and a
publish path that depends on someone else's billing is not a publish path.

The build itself happens in a one-shot docker CLI container (BUILD_IMAGE)
that attaches to a BuildKit builder the host created with memory and CPU
caps, so a build can never take the box down with it. The repo is a remote
git context fetched by BuildKit with the console's GitHub token, so nothing
is checked out on the box and no git credentials live there. Builds are
serialized console-wide by one lock: the box serves while it builds, and one
capped build at a time is the budget.

The console never builds its own image through this: that would be circular
(a bad build breaks the thing that fixes it). Its image ships out-of-band."""

import asyncio
import logging
import time

import docker.errors
from sqlalchemy.ext.asyncio import AsyncSession

from console import alerts, config, github, settings_store
from console.db.models import Deployment, Project, utcnow
from console.db.session import SessionLocal
from console.deploy import engine as deploy_engine
from console.docker.client import get_client, run
from console.schema.console_toml import ConfigError, parse_console_toml

logger = logging.getLogger(__name__)

CONSOLE_TOML = "console.toml"
DOCKER_SOCKET = "/var/run/docker.sock"

_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()

_TRUNCATED = "\n... build output truncated ...\n"

# What runs inside the CLI container. Everything variable arrives as an
# environment variable, never interpolated into the script, so a repo name
# or tag can never become shell. The remote driver attaches this throwaway
# client to the builder the host bootstrapped once; its BuildKit container is
# what carries the caps and the layer cache. --provenance=false keeps the
# push a plain single-platform image, which is what the engine's pull expects.
SCRIPT = """\
set -eu
printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin >/dev/null
docker buildx create --name "$BUILDER" --driver remote "docker-container://buildx_buildkit_${BUILDER}0" >/dev/null
docker buildx build --builder "$BUILDER" --platform linux/amd64 --provenance=false \\
  --secret id=GIT_AUTH_TOKEN,env=GIT_AUTH_TOKEN -f "$DOCKERFILE" -t "$IMAGE" --push "$CONTEXT"
docker buildx prune --builder "$BUILDER" --reserved-space 5g -f >/dev/null
"""


class BuildFailed(Exception):
    """Mark the deployment failed with this message and stop."""


def enqueue(deployment_id: str) -> None:
    """Spawn a build task for a deployment that is in "building"."""
    task = asyncio.create_task(run_build(deployment_id))
    _tasks.add(task)  # strong ref; asyncio only keeps weak ones
    task.add_done_callback(_tasks.discard)


def image_for(repo: str, sha: str) -> str:
    """The image the build publishes: the same name and tag the Actions
    workflow used, so history reads the same either way. GHCR requires a
    lowercase path; a repo can be registered in any case."""
    return f"ghcr.io/{repo.lower()}:{sha[:7]}"


def git_context(repo: str, sha: str) -> str:
    return f"https://github.com/{repo}.git#{sha}"


async def run_build(deployment_id: str) -> None:
    async with SessionLocal() as session:
        deployment = await session.get(Deployment, deployment_id)
        if deployment is None:
            return
        async with _lock:
            # Claim check: reaped or failed while waiting for the lock
            await session.refresh(deployment)
            if deployment.status != "building":
                return
            project = await session.get(Project, deployment.project_id)
            if project is None:
                return
            try:
                await _build(session, deployment, project)
            except BuildFailed as exc:
                await _fail(session, deployment, project, str(exc))
            except Exception as exc:  # tasks must never die silently
                await _fail(session, deployment, project, f"internal error: {exc!r}")


async def _build(session: AsyncSession, deployment: Deployment, project: Project) -> None:
    # Everything that can fail before Docker is touched, fails here first.
    try:
        github_token = await github.resolve_token(session)
    except github.GitHubNotConnected as exc:
        raise BuildFailed(str(exc))
    ghcr_token = await settings_store.get(session, settings_store.GHCR_TOKEN)
    if not ghcr_token:
        raise BuildFailed(
            "no GitHub packages token in Settings. The console pushes what it "
            "builds, so the token needs write:packages as well as read:packages."
        )
    short = deployment.sha[:7]
    try:
        text = await github.GitHub(github_token).read_file(
            project.repo, CONSOLE_TOML, deployment.sha
        )
    except github.FileNotFound:
        raise BuildFailed(f"no {CONSOLE_TOML} in {project.repo} at {short}")
    except github.GitHubApiError as exc:
        raise BuildFailed(str(exc))
    try:
        cfg, warnings = parse_console_toml(text)
    except ConfigError as exc:
        raise BuildFailed(str(exc))

    image = image_for(project.repo, deployment.sha)
    await _log(session, deployment, f"building {image} from {project.repo}@{short}")
    for warning in warnings:
        await _log(session, deployment, f"warning: {warning}")

    client = get_client()
    name = f"console-build-{deployment.id[:6]}"
    try:
        container = await run(
            client.containers.run,
            config.BUILD_IMAGE,
            ["sh", "-c", SCRIPT],
            detach=True,
            name=name,
            environment={
                "GHCR_USER": config.OIDC_OWNER,
                "GHCR_TOKEN": ghcr_token,
                "GIT_AUTH_TOKEN": github_token,
                "BUILDER": config.BUILD_BUILDER,
                "DOCKERFILE": cfg.app.dockerfile,
                "IMAGE": image,
                "CONTEXT": git_context(project.repo, deployment.sha),
            },
            volumes={DOCKER_SOCKET: {"bind": DOCKER_SOCKET, "mode": "rw"}},
        )
    except docker.errors.DockerException as exc:
        raise BuildFailed(f"could not start the build container: {exc}")
    try:
        exit_code = await _drain(session, deployment, container)
    finally:
        # The container held two tokens in its environment; it does not outlive
        # the build, whichever way the build went.
        try:
            await run(container.remove, force=True)
        except Exception:
            logger.warning("build container %s was not removed", name, exc_info=True)
    if exit_code != 0:
        raise BuildFailed(f"build exited {exit_code}")

    deployment.image = image
    deployment.config_snapshot = cfg.model_dump_json()
    deployment.build_finished_at = utcnow()
    deployment.status = "queued"
    await _log(session, deployment, f"pushed {image}")
    await deploy_engine.queue(session, deployment)


async def _drain(session: AsyncSession, deployment: Deployment, container) -> int:
    """Stream the build output into the row until the container exits.
    Returns its exit code. Past BUILD_TIMEOUT the container is killed, which
    also ends a build that hung without printing anything."""
    stream = await run(container.logs, stream=True, follow=True)
    deadline = time.monotonic() + config.BUILD_TIMEOUT
    truncated = False
    while True:
        remaining = deadline - time.monotonic()
        try:
            chunk = await asyncio.wait_for(
                asyncio.to_thread(next, stream, None), timeout=max(remaining, 0)
            )
        except TimeoutError:
            try:
                await run(container.kill)
            except Exception:
                pass
            raise BuildFailed(f"build outran {int(config.BUILD_TIMEOUT) // 60} minutes")
        if chunk is None:
            break
        if not truncated:
            truncated = await _append(
                session, deployment, chunk.decode("utf-8", errors="replace")
            )
    result = await run(container.wait)
    return int(result.get("StatusCode", -1))


async def _append(session: AsyncSession, deployment: Deployment, text: str) -> bool:
    """Append output and commit so the poller sees progress. Returns True once
    the stored log has hit BUILD_LOG_MAX (the caller stops appending but keeps
    draining so the exit code is still captured)."""
    current = deployment.log or ""
    room = config.BUILD_LOG_MAX - len(current)
    if room <= 0:
        return True
    if len(text) > room:
        deployment.log = current + text[:room] + _TRUNCATED
        await session.commit()
        return True
    deployment.log = current + text
    await session.commit()
    return False


async def _log(session: AsyncSession, deployment: Deployment, line: str) -> None:
    deployment.log = (deployment.log or "") + line + "\n"
    await session.commit()


async def _fail(
    session: AsyncSession, deployment: Deployment, project: Project, reason: str
) -> None:
    deployment.status = "failed"
    deployment.failure_reason = reason
    deployment.finished_at = utcnow()
    deployment.log = (deployment.log or "") + f"failed: {reason}\n"
    await session.commit()
    # Best-effort alert; never let it break the fail path.
    try:
        await alerts.send(
            session,
            f"{project.name} build failed",
            f"{deployment.sha[:7]}: {reason}",
            tags=["rotating_light"],
            priority="high",
        )
    except Exception:
        logger.warning("build-failure alert failed", exc_info=True)
