"""Where encrypted backup bundles are pushed off-box. One implementation today,
a private GitHub repo over the contents API (no extra dependency, reuses the
PAT-in-settings pattern). The BackupStore protocol keeps the engine ignorant of
the destination so an object-store backend (R2, S3) can drop in later."""

import base64
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from console import settings_store

GITHUB_API = "https://api.github.com"
BACKUP_DIR = "backups"


class BackupStoreError(Exception):
    """The destination rejected an operation."""


class BackupStore(Protocol):
    async def put(self, name: str, data: bytes) -> str: ...
    async def names(self) -> list[str]: ...
    async def delete(self, name: str) -> None: ...


class GitHubBackupStore:
    """Stores each bundle as a file under backups/ in a private repo."""

    def __init__(self, token: str, repo: str) -> None:
        self._repo = repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"{GITHUB_API}/repos/{self._repo}/contents/{path}"

    async def put(self, name: str, data: bytes) -> str:
        path = f"{BACKUP_DIR}/{name}"
        body = {
            "message": f"backup {name}",
            "content": base64.b64encode(data).decode(),
        }
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.put(self._url(path), headers=self._headers, json=body)
        if resp.status_code not in (200, 201):
            raise BackupStoreError(
                f"github upload failed: {resp.status_code} {_detail(resp)}"
            )
        return f"github:{self._repo}/{path}"

    async def names(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(self._url(BACKUP_DIR), headers=self._headers)
        if resp.status_code == 404:
            return []  # the backups/ dir does not exist until the first put
        if resp.status_code != 200:
            raise BackupStoreError(
                f"github list failed: {resp.status_code} {_detail(resp)}"
            )
        return [item["name"] for item in resp.json() if item.get("type") == "file"]

    async def delete(self, name: str) -> None:
        path = f"{BACKUP_DIR}/{name}"
        async with httpx.AsyncClient(timeout=30) as http:
            head = await http.get(self._url(path), headers=self._headers)
            if head.status_code == 404:
                return
            if head.status_code != 200:
                raise BackupStoreError(
                    f"github stat failed: {head.status_code} {_detail(head)}"
                )
            sha = head.json()["sha"]
            resp = await http.request(
                "DELETE",
                self._url(path),
                headers=self._headers,
                json={"message": f"prune {name}", "sha": sha},
            )
        if resp.status_code != 200:
            raise BackupStoreError(
                f"github delete failed: {resp.status_code} {_detail(resp)}"
            )


def _detail(resp: httpx.Response) -> str:
    try:
        return resp.json().get("message", resp.text)
    except ValueError:
        return resp.text


async def resolve_store(session: AsyncSession) -> BackupStore | None:
    """Build the configured store, or None if the destination is not set up."""
    token = await settings_store.get(session, settings_store.BACKUP_GITHUB_TOKEN)
    repo = await settings_store.get(session, settings_store.BACKUP_GITHUB_REPO)
    if not token or not repo:
        return None
    return GitHubBackupStore(token, repo)
