import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from console.api.backups import router as backups_router
from console.api.commands import router as commands_router
from console.api.containers import router as containers_router
from console.api.deployments import router as deployments_router
from console.api.hooks import router as hooks_router
from console.api.projects import router as projects_router
from console.api.secrets import router as secrets_router
from console.api.settings import router as settings_router
from console.api.terminal import router as terminal_router
from console.api.validate import router as validate_router
from console.db.models import CommandRun, Deployment, utcnow
from console.db.session import SessionLocal
from console.deploy import engine as deploy_engine
from console.cloudflare import AccessNotConfigured
from console.backup.engine import backup_loop
from console.deploy.reaper import reaper_loop
from console.secrets.crypto import KeyNotConfigured

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
    yield
    for task in (reaper, backups):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="console", lifespan=lifespan)
app.include_router(backups_router)
app.include_router(commands_router)
app.include_router(containers_router)
app.include_router(deployments_router)
app.include_router(hooks_router)
app.include_router(projects_router)
app.include_router(secrets_router)
app.include_router(settings_router)
app.include_router(terminal_router)
app.include_router(validate_router)


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
