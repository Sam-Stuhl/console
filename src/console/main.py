import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from console.api.access import router as access_router
from console.api.alerts import router as alerts_router
from console.api.backups import router as backups_router
from console.api.commands import router as commands_router
from console.api.credentials import router as credentials_router
from console.api.containers import router as containers_router
from console.api.controls import router as controls_router
from console.api.deployments import router as deployments_router
from console.api.domains import router as domains_router
from console.api.github import router as github_router
from console.api.hooks import router as hooks_router
from console.api.projects import router as projects_router
from console.api.secrets import router as secrets_router
from console.api.settings import router as settings_router
from console.api.terminal import router as terminal_router
from console.api.tokens import router as tokens_router
from console.api.validate import router as validate_router
from console.db.models import CommandRun, Deployment, utcnow
from console.db.session import SessionLocal
from console.deploy import engine as deploy_engine
from console.cloudflare import AccessNotConfigured
from console.backup.engine import backup_loop
from console.deploy.reaper import reaper_loop
from console.deploy.watcher import watch_loop
from console.monitor import monitor_loop
from console.secrets.crypto import KeyNotConfigured
from console.v1 import mcp as v1_mcp, rest as v1_rest
from console.v1.rest import router as v1_router

# Built at import time so the lifespan below can run the session manager.
_mcp_sessions = v1_mcp.build_transport()

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"


def _run_migrations() -> None:
    cfg = AlembicConfig(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # In a thread: the async alembic env calls asyncio.run(), which is
    # illegal on uvicorn's already-running loop.
    await asyncio.to_thread(_run_migrations)

    # Deploy tasks do not survive a restart; pick queued rows back up.
    # Rows orphaned mid-deploy are the reaper's job.
    async with SessionLocal() as session:
        queued = await session.scalars(
            select(Deployment.id).where(Deployment.status == "queued")
        )
        for deployment_id in queued:
            deploy_engine.enqueue(deployment_id)

        # An in-flight command exec cannot be resumed, so any run still marked
        # running is orphaned by the restart. Fail it rather than leave it hung.
        orphans = await session.scalars(
            select(CommandRun).where(CommandRun.status == "running")
        )
        for cmd_run in orphans:
            cmd_run.status = "failed"
            cmd_run.failure_reason = "interrupted by a console restart"
            cmd_run.finished_at = utcnow()
        await session.commit()

    reaper = asyncio.create_task(reaper_loop())
    backups = asyncio.create_task(backup_loop())
    monitor = asyncio.create_task(monitor_loop())
    watcher = asyncio.create_task(watch_loop())
    # The MCP transport keeps per-client sessions, which need a running manager;
    # without this every /mcp request fails.
    async with _mcp_sessions.run():
        yield
    for task in (reaper, backups, monitor, watcher):
        task.cancel()
        # A loop that already died on a bug logged its own reason; shutdown
        # only needs the task settled, and the ones after it still cancelled.
        with suppress(asyncio.CancelledError, Exception):
            await task


app = FastAPI(title="console", lifespan=lifespan)
app.include_router(access_router)
app.include_router(alerts_router)
app.include_router(backups_router)
app.include_router(commands_router)
app.include_router(containers_router)
app.include_router(controls_router)
app.include_router(credentials_router)
app.include_router(deployments_router)
app.include_router(domains_router)
app.include_router(github_router)
app.include_router(hooks_router)
app.include_router(projects_router)
app.include_router(secrets_router)
app.include_router(settings_router)
app.include_router(terminal_router)
app.include_router(tokens_router)
app.include_router(validate_router)
app.include_router(v1_router)
v1_rest.install_error_handler(app)
# Middleware rather than a mount: it has to answer the bare /mcp, which
# Starlette's Mount does not match, and it must run before the SPA catch-all.
app.add_middleware(v1_mcp.McpGate)


# The version-scoped spec and its docs page are deliberately NOT token-gated:
# they describe the shape of the API and carry no data, so an agent can be
# pointed straight at the spec. Every route that returns anything real needs a
# token.
@app.get("/v1/openapi.json", include_in_schema=False)
async def v1_openapi() -> dict:
    return v1_rest.openapi_schema()


@app.get("/v1/docs", include_in_schema=False)
async def v1_docs() -> HTMLResponse:
    return get_swagger_ui_html(
        openapi_url="/v1/openapi.json", title=f"{v1_rest.TITLE} v{v1_rest.VERSION}"
    )


@app.exception_handler(KeyNotConfigured)
async def _key_not_configured(_request, exc: KeyNotConfigured):
    # Secret operations fail loudly; everything else keeps working.
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(AccessNotConfigured)
async def _access_not_configured(_request, exc: AccessNotConfigured):
    # The access toggle 503s without a Cloudflare token; the rest is unaffected.
    return JSONResponse(status_code=503, content={"detail": str(exc)})

# In production the built SPA is served straight from FastAPI: one process,
# one container. In dev the Vite server serves the frontend and proxies /api
# here, so this block is simply inactive until `npm run build` has run.
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = DIST / path
        if path and ".." not in path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
