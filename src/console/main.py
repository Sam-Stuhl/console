import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from console.api.containers import router as containers_router

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
    yield


app = FastAPI(title="console", lifespan=lifespan)
app.include_router(containers_router)

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
