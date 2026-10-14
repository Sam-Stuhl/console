"""Outbound Forgejo access, for projects whose code lives on a Forgejo (or
Gitea) instance the operator runs instead of on GitHub.

The same narrow slice the GitHub client offers the builder and the watcher:
resolve a ref to a commit, read console.toml at a sha. Repo listing and the
OAuth connection stay GitHub-only; a Forgejo project's repo is typed in.

The instance is reached at the URL saved in Settings, which is normally an
address on the console's own network (the Forgejo container's alias, say
http://forgejo:3000), so the console never has to pass whatever login sits in
front of the public hostname. The token is a Forgejo access token with read
access to the repositories the console builds.

Errors subclass the GitHub client's, so every caller that already handles
"not connected", "no such file" and "the API refused" handles both providers
with the same except clauses."""

import re

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, settings_store
from console.github import FileNotFound, GitHubApiError, GitHubNotConnected, REPO_RE

__all__ = ["FileNotFound", "Forgejo", "ForgejoApiError", "ForgejoNotConnected", "resolve"]

URL_RE = re.compile(r"^https?://[^\s/]+(/[^\s]*)?$")


class ForgejoNotConnected(GitHubNotConnected):
    def __init__(self) -> None:
        Exception.__init__(
            self,
            "The console is not connected to Forgejo. Save the Forgejo URL and an "
            "access token in Settings to build projects from it.",
        )


class ForgejoApiError(GitHubApiError):
    """A Forgejo API call failed; carries a readable reason for the UI."""


def _checked_repo(repo: str) -> str:
    if not REPO_RE.match(repo):
        raise ForgejoApiError(f'"{repo}" is not a valid owner/repo')
    return repo


async def resolve(session: AsyncSession) -> tuple[str, str]:
    """The instance URL (no trailing slash) and token, or ForgejoNotConnected."""
    url = (await settings_store.get(session, settings_store.FORGEJO_URL) or "").strip()
    token = await settings_store.get(session, settings_store.FORGEJO_TOKEN)
    if not url or not token:
        raise ForgejoNotConnected()
    if not URL_RE.match(url):
        raise ForgejoApiError(f'the saved Forgejo URL "{url}" is not an http(s) URL')
    return url.rstrip("/"), token


def clone_url(base_url: str, repo: str) -> str:
    return f"{base_url}/{_checked_repo(repo)}.git"


class Forgejo:
    """The slice of the Forgejo API this console uses."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"token {token}", "Accept": "application/json"}

    async def resolve_commit(self, repo: str, ref: str) -> tuple[str, str]:
        """The full sha and message of the commit a branch, tag or sha names.
        Raises FileNotFound when there is no such ref."""
        resp = await self._get(
            f"/repos/{_checked_repo(repo)}/commits",
            params={
                "sha": ref,
                "limit": 1,
                "stat": "false",
                "verification": "false",
                "files": "false",
            },
        )
        data = _json(resp, repo)
        if not isinstance(data, list):
            raise ForgejoApiError(f"Forgejo returned an unexpected commit list for {repo}")
        if not data:
            raise FileNotFound(ref)
        sha = data[0].get("sha")
        if not isinstance(sha, str) or not sha:
            raise ForgejoApiError(f'Forgejo returned no commit for "{ref}" in {repo}')
        message = (data[0].get("commit") or {}).get("message") or ""
        return sha, message

    async def read_file(self, repo: str, path: str, ref: str) -> str:
        """A text file from a repo at a ref, or FileNotFound."""
        resp = await self._get(
            f"/repos/{_checked_repo(repo)}/raw/{path}", params={"ref": ref}
        )
        if len(resp.content) > config.GITHUB_FILE_MAX_BYTES:
            raise ForgejoApiError(f"{path} in {repo} is larger than we will read")
        try:
            return resp.content.decode()
        except UnicodeDecodeError:
            raise ForgejoApiError(f"{path} in {repo} is not text")

    async def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=config.GITHUB_TIMEOUT) as http:
                resp = await http.get(
                    f"{self._base}/api/v1{path}", headers=self._headers, params=params
                )
        except httpx.HTTPError as exc:
            raise ForgejoApiError(f"could not reach Forgejo: {exc}")
        if resp.status_code == 404:
            raise FileNotFound(path)
        if resp.status_code == 401:
            raise ForgejoApiError(
                "Forgejo rejected the stored token. Save a new one in Settings."
            )
        if not resp.is_success:
            raise ForgejoApiError(f"Forgejo API {resp.status_code}: {_detail(resp)}")
        return resp


def _json(resp: httpx.Response, repo: str):
    try:
        return resp.json()
    except ValueError:
        raise ForgejoApiError(f"Forgejo returned non-JSON for {repo}")


def _detail(resp: httpx.Response) -> str:
    try:
        return resp.json().get("message", resp.text[:200])
    except (ValueError, AttributeError):
        return resp.text[:200]
