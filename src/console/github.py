"""Outbound GitHub access: list the operator's repos when registering a
project, and read a repo's console.toml when deploying an image CI did not
deploy for us.

This is a credential the console uses to call GitHub AS the operator. It is not
a way to log in to the console: no session, no user record, nothing gated by it.
Cloudflare Access is still the only inbound gate, and these calls only ever go
outward.

The token comes from the OAuth device flow, which suits this app: no callback
URL to route through Access, no client secret to ship in a public repo, and
completion is polled rather than streamed. The device code is handed to the
browser and sent back on each poll, so the console keeps no pending state; a
device code is single-use, expires in minutes, and is worthless until the
operator approves it on github.com.

Without a token every call raises GitHubNotConnected, and the features that
need it degrade instead of breaking (the repo field stays free text, the deploy
form takes a pasted console.toml).
"""

import base64
import re
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from console import config, settings_store

# "owner/name", the same shape the projects API accepts. Repo names reach the
# request path, so anything else is refused before it can steer a call at a
# different endpoint.
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def _checked_repo(repo: str) -> str:
    if not REPO_RE.match(repo):
        raise GitHubApiError(f'"{repo}" is not a valid owner/repo')
    return repo


class GitHubNotConnected(Exception):
    def __init__(self) -> None:
        super().__init__(
            "The console is not connected to GitHub. Connect an account in "
            "Settings to pick repos and read console.toml from them."
        )


class GitHubApiError(Exception):
    """A GitHub API call failed; carries a readable reason for the UI."""


class FileNotFound(Exception):
    """No such file at that path/ref. Distinct from an API failure so the
    caller can say which one happened."""


class NotSetUp(Exception):
    def __init__(self) -> None:
        super().__init__(
            "The GitHub OAuth app is not set up. Save its client id and client "
            "secret in Settings, and set its callback URL to this console."
        )


async def resolve_token(session: AsyncSession) -> str:
    token = await settings_store.get(session, settings_store.GITHUB_TOKEN)
    if not token:
        raise GitHubNotConnected()
    return token


async def client_id(session: AsyncSession) -> str:
    """The OAuth app to authorize against: the one saved in Settings, falling
    back to CONSOLE_GITHUB_CLIENT_ID. Settings first for the same reason the
    Cloudflare credentials work that way, so an operator can set this up in the
    browser without editing a file on the box and restarting.

    Returns "" when neither is set, which means the feature is simply off."""
    stored = await settings_store.get(session, settings_store.GITHUB_CLIENT_ID)
    return stored or config.GITHUB_CLIENT_ID


async def app_credentials(session: AsyncSession) -> tuple[str, str]:
    """Client id and secret. Both are needed to complete the redirect, so
    either one missing means the same thing: not set up yet."""
    identifier = await client_id(session)
    secret = await settings_store.get(session, settings_store.GITHUB_CLIENT_SECRET)
    if not identifier or not secret:
        raise NotSetUp()
    return identifier, secret


# --- authorization code flow ------------------------------------------------


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Where to send the operator's browser to approve the console."""
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": config.GITHUB_SCOPE,
            "state": state,
        }
    )
    return f"{config.GITHUB_AUTHORIZE_URL}?{query}"


async def exchange_code(
    client_id: str, client_secret: str, code: str, redirect_uri: str
) -> str:
    """Trade the code GitHub handed back for a token. This is the whole point
    of the redirect: the answer is immediate and definite, so the console knows
    the authorization worked instead of inferring it."""
    data = await _post_form(
        config.GITHUB_TOKEN_URL,
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )
    token = data.get("access_token")
    if not token:
        raise GitHubApiError(f"GitHub would not exchange the code: {_error(data)}")
    return token


async def _post_form(url: str, form: dict[str, str]) -> dict:
    try:
        async with httpx.AsyncClient(timeout=config.GITHUB_TIMEOUT) as http:
            resp = await http.post(url, data=form, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise GitHubApiError(f"could not reach GitHub: {exc}")
    try:
        return resp.json()
    except ValueError:
        raise GitHubApiError(f"GitHub returned {resp.status_code}: {resp.text[:200]}")


def _error(data: dict) -> str:
    return data.get("error_description") or data.get("error") or "no reason given"


# --- API client ------------------------------------------------------------


class GitHub:
    """The narrow slice of the GitHub API this console uses."""

    def __init__(self, token: str) -> None:
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def login(self) -> str:
        """The connected account's username. Doubles as a token liveness check."""
        data = await self._get("/user")
        return data.get("login", "")

    async def list_repos(self) -> list[dict]:
        """Repos the connected account owns, most recently pushed first.

        Filtered to CONSOLE_OIDC_OWNER, because a project under any other owner
        could never deploy anyway: oidc.verify rejects it. With no owner set
        nothing can deploy at all, so filtering would only hide everything;
        show what the token can see and let the OIDC check speak for itself.
        """
        repos = await self._get(
            "/user/repos",
            params={
                "affiliation": "owner",
                "sort": "pushed",
                "per_page": config.GITHUB_PAGE_SIZE,
            },
        )
        if not isinstance(repos, list):
            raise GitHubApiError("GitHub returned an unexpected repo list")
        owner = config.OIDC_OWNER.lower()
        return [
            {
                "full_name": repo["full_name"],
                "default_branch": repo.get("default_branch") or "main",
                "private": bool(repo.get("private")),
                # The list comes back most-recently-pushed first; showing when
                # is what makes that order legible rather than arbitrary.
                "pushed_at": repo.get("pushed_at"),
            }
            for repo in repos
            if not owner or repo["full_name"].split("/", 1)[0].lower() == owner
        ]

    async def list_branches(self, repo: str) -> list[str]:
        """Branch names in a repo, so registering a project can offer them
        rather than trusting a typed one. One page: a picker, not a browser."""
        branches = await self._get(
            f"/repos/{_checked_repo(repo)}/branches",
            params={"per_page": config.GITHUB_PAGE_SIZE},
        )
        if not isinstance(branches, list):
            raise GitHubApiError("GitHub returned an unexpected branch list")
        return [b["name"] for b in branches if b.get("name")]

    async def read_file(self, repo: str, path: str, ref: str) -> str:
        """A text file from a repo at a ref. Raises FileNotFound if there is
        no such file, which is a different thing from GitHub being unhappy."""
        data = await self._get(
            f"/repos/{_checked_repo(repo)}/contents/{path}", params={"ref": ref}
        )
        # The contents API answers with base64 wrapped in JSON, and files over
        # ~1MB come back with an empty content field and a download_url
        # instead. Decoding that blindly yields empty or corrupt text that
        # looks like a broken file rather than a too-big one, so check first.
        if data.get("type") != "file" or not data.get("content"):
            raise GitHubApiError(
                f"{path} in {repo} is not a readable file (too large, or a directory)"
            )
        if data.get("encoding") != "base64":
            raise GitHubApiError(f"unexpected encoding for {path}: {data.get('encoding')}")
        if data.get("size", 0) > config.GITHUB_FILE_MAX_BYTES:
            raise GitHubApiError(f"{path} in {repo} is larger than we will read")
        try:
            # The payload is newline-wrapped; b64decode ignores the newlines.
            raw = base64.b64decode(data["content"])
        except ValueError as exc:
            raise GitHubApiError(f"could not decode {path} from {repo}: {exc}")
        try:
            return raw.decode()
        except UnicodeDecodeError:
            raise GitHubApiError(f"{path} in {repo} is not text")

    async def resolve_commit(self, repo: str, ref: str) -> tuple[str, str]:
        """The full sha and message of the commit a ref names. The ref may be
        a branch, a tag, or a sha (short or full); GitHub resolves all three.
        Raises FileNotFound when there is no such ref, which is a different
        thing from GitHub being unhappy."""
        data = await self._get(f"/repos/{_checked_repo(repo)}/commits/{ref}")
        sha = data.get("sha") if isinstance(data, dict) else None
        if not isinstance(sha, str) or not sha:
            raise GitHubApiError(f'GitHub returned no commit for "{ref}" in {repo}')
        message = (data.get("commit") or {}).get("message") or ""
        return sha, message

    async def _get(self, path: str, params: dict | None = None):
        try:
            async with httpx.AsyncClient(timeout=config.GITHUB_TIMEOUT) as http:
                resp = await http.get(
                    config.GITHUB_API + path, headers=self._headers, params=params
                )
        except httpx.HTTPError as exc:
            raise GitHubApiError(f"could not reach GitHub: {exc}")
        if resp.status_code == 404:
            raise FileNotFound(path)
        if resp.status_code == 401:
            raise GitHubApiError(
                "GitHub rejected the stored token. Reconnect the account in Settings."
            )
        if not resp.is_success:
            raise GitHubApiError(f"GitHub API {resp.status_code}: {_detail(resp)}")
        try:
            return resp.json()
        except ValueError:
            raise GitHubApiError(f"GitHub returned non-JSON for {path}")


def _detail(resp: httpx.Response) -> str:
    try:
        return resp.json().get("message", resp.text[:200])
    except ValueError:
        return resp.text[:200]
