"""Deploying an image that already exists, with no build webhook involved.

Nothing is built here: the console pulls what CI, or a laptop, already pushed.
This is the path for a project whose CI has never run or is broken, since build
and deploy are separate concerns and an image sitting in a registry should be
reachable without Actions being healthy.

Lives here rather than in a route body because both the SPA's /api and the
machine-facing /v1 offer it, and the rules (what counts as a valid image, where
the config comes from) are facts about deploying, not about who asked."""

from sqlalchemy.ext.asyncio import AsyncSession

from console import github
from console.db.models import Deployment, Project
from console.deploy import engine as deploy_engine, plan
from console.errors import Invalid, Unavailable, Upstream
from console.schema.console_toml import ConfigError, parse_console_toml

CONSOLE_TOML = "console.toml"


async def resolve_config(
    session: AsyncSession, project: Project, ref: str, pasted: str | None
) -> str:
    """The console.toml for a manual deploy, as a validated config snapshot.

    Read from the repo unless one was pasted: the file in the repo is the
    source of truth, and reading it means an image built anywhere can be
    deployed without retyping its config."""
    if pasted is None:
        try:
            pasted = await github.GitHub(
                await github.resolve_token(session)
            ).read_file(project.repo, CONSOLE_TOML, ref)
        except github.GitHubNotConnected as exc:
            raise Unavailable(str(exc))
        except github.FileNotFound:
            raise Invalid(f'no {CONSOLE_TOML} in {project.repo} at "{ref}"')
        except github.GitHubApiError as exc:
            raise Upstream(str(exc))
    try:
        parsed, _ = parse_console_toml(pasted)
    except ConfigError as exc:
        raise Invalid(str(exc))
    return parsed.model_dump_json()


async def deploy_image(
    session: AsyncSession,
    project: Project,
    image: str,
    ref: str | None = None,
    console_toml: str | None = None,
) -> Deployment:
    """Queue a deployment of an existing image. Returns the new row."""
    try:
        tag = plan.validate_image(image)
    except ValueError as exc:
        raise Invalid(str(exc))

    git_ref = (ref or project.branch).strip() or project.branch
    config_snapshot = await resolve_config(session, project, git_ref, console_toml)

    # The tag identifies the build, the way a commit sha does for a CI deploy:
    # our own workflow tags with the short sha, so those line up, and any other
    # tag is kept verbatim so history reads as what was actually deployed.
    deployment = Deployment(
        project_id=project.id,
        sha=tag,
        commit_message=f"manual deploy of {tag}",
        image=image.strip(),
        config_snapshot=config_snapshot,
        status="queued",
    )
    session.add(deployment)
    await deploy_engine.queue(session, deployment)
    return deployment
