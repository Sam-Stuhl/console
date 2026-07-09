from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from console.api.containers import router as containers_router

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

app = FastAPI(title="console")
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
