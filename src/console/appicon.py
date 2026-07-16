"""Pull an app's own favicon from its running container, so a project's icon
comes from the app itself and never has to be pasted into the console.

The fetch is container-to-container (http://{container}:{port}/...), exactly
like the health monitor, so it bypasses Cloudflare Access and works even for
login-protected apps. It mirrors how a browser finds a favicon: read the root
HTML, follow a <link rel="...icon...">, and fall back to /favicon.svg then
/favicon.ico. From host uvicorn container names do not resolve, so in dev this
just no-ops (the dev limitation) and the UI keeps showing initials."""

import logging
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from console import config
from console.db.models import Deployment, Project, utcnow
from console.docker.containers import find_project_container
from console.schema.console_toml import ConsoleConfig

logger = logging.getLogger(__name__)

_ICON_RELS = {"icon", "shortcut icon", "apple-touch-icon", "apple-touch-icon-precomposed"}


class _IconLinkParser(HTMLParser):
    """Collects the href of every <link rel~=icon> in document order."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        rels = {r.strip().lower() for r in attr.get("rel", "").split()}
        if rels & _ICON_RELS and attr.get("href"):
            self.hrefs.append(attr["href"])


async def _base_url(session: AsyncSession, project: Project) -> str | None:
    """http://{container}:{port}/ for a deploy-live project, else None."""
    live = await session.scalar(
        select(Deployment).where(
            Deployment.project_id == project.id, Deployment.status == "live"
        )
    )
    if live is None or not live.config_snapshot:
        return None
    container = await find_project_container(project.id)
    if container is None:
        return None
    cfg = ConsoleConfig.model_validate_json(live.config_snapshot)
    return f"http://{container.name}:{cfg.app.port}/"


async def _candidate_urls(client: httpx.AsyncClient, base: str) -> list[str]:
    """Icon URLs to try, best first: links from the root HTML (svg preferred),
    then the conventional /favicon.svg and /favicon.ico."""
    urls: list[str] = []
    try:
        resp = await client.get(base)
        if resp.status_code == 200 and resp.text:
            parser = _IconLinkParser()
            parser.feed(resp.text)
            svg = [h for h in parser.hrefs if _ext(h) == ".svg"]
            for href in svg + [h for h in parser.hrefs if h not in svg]:
                urls.append(urljoin(base, href))
    except httpx.HTTPError:
        pass
    for fallback in ("favicon.svg", "favicon.ico"):
        url = urljoin(base, fallback)
        if url not in urls:
            urls.append(url)
    return urls


def _ext(url: str) -> str:
    path = url.lower().split("?")[0].split("#")[0]
    dot = path.rfind(".")
    return path[dot:] if dot != -1 else ""


def _acceptable(ctype: str, url: str, data: bytes) -> bool:
    """Real image, by content type or (for mislabeled servers) magic bytes."""
    if ctype.startswith("image/"):
        return True
    ext = _ext(url)
    if ext == ".svg":
        return b"<svg" in data[:512].lower()
    if ext == ".ico":
        return data[:4] == b"\x00\x00\x01\x00"
    if ext == ".png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    return False


def _content_type(ctype: str, url: str) -> str:
    if ctype.startswith("image/"):
        return ctype
    return {".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png"}.get(
        _ext(url), ctype or "application/octet-stream"
    )


async def _try_fetch(client: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    try:
        resp = await client.get(url)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    data = resp.content
    if not data or len(data) > config.ICON_MAX_BYTES:
        return None
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not _acceptable(ctype, url, data):
        return None
    return data, _content_type(ctype, url)


async def fetch_and_store(session: AsyncSession, project: Project) -> bool:
    """Fetch the app's favicon and store it on the project. Returns whether one
    was found. Leaves any existing icon untouched when nothing is found."""
    base = await _base_url(session, project)
    if base is None:
        return False
    async with httpx.AsyncClient(
        timeout=config.ICON_TIMEOUT, follow_redirects=True
    ) as client:
        for url in await _candidate_urls(client, base):
            found = await _try_fetch(client, url)
            if found is not None:
                project.icon_data, project.icon_content_type = found
                project.icon_fetched_at = utcnow()
                return True
    return False
