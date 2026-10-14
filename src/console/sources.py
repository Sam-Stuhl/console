"""Where a project's code lives, which is a per-project setting.

"github" is the default and what every project registered before Forgejo
support reads as (the column's server default), so nothing about an existing
project changes. "forgejo" reads the repo from the Forgejo instance saved in
Settings. Either way the image goes to the same GHCR namespace, so pulling,
the namespace check and rollback are the same for both."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, forgejo, github
from console.db.models import Project

GITHUB = "github"
FORGEJO = "forgejo"
PROVIDERS = (GITHUB, FORGEJO)


def check_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(
            f'git provider "{provider}" is not one of: {", ".join(PROVIDERS)}'
        )
    return provider


async def client(session: AsyncSession, provider: str) -> github.GitHub | forgejo.Forgejo:
    """An API client for the provider. Raises GitHubNotConnected (or its
    ForgejoNotConnected subclass) when that provider has no credentials."""
    if provider == FORGEJO:
        url, token = await forgejo.resolve(session)
        return forgejo.Forgejo(url, token)
    return github.GitHub(await github.resolve_token(session))


def image_repo(project: Project) -> str:
    """The GHCR path, minus registry and tag. A GitHub project keeps the path
    its Actions workflow always used. A Forgejo owner is not a GHCR namespace,
    so a Forgejo project's image goes under the trusted owner instead, named
    after the repo."""
    if project.git_provider == FORGEJO:
        name = project.repo.split("/", 1)[-1]
        return f"{config.OIDC_OWNER.lower()}/{name.lower()}"
    return project.repo.lower()


async def image_conflict(session: AsyncSession, project: Project) -> Project | None:
    """Another project that would publish to the same GHCR path. A Forgejo
    project's path drops its owner, so a Forgejo "x/app" and a GitHub
    "<owner>/app" (or two Forgejo owners' "app") would overwrite each other's
    images; the repo column's uniqueness does not catch that."""
    target = image_repo(project)
    for other in await session.scalars(select(Project)):
        if other.id != project.id and other is not project and image_repo(other) == target:
            return other
    return None


def image_for(project: Project, sha: str) -> str:
    return f"ghcr.io/{image_repo(project)}:{sha[:7]}"


def image_hint(project: Project) -> str:
    return f"ghcr.io/{image_repo(project)}:"
