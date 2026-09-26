"""FastAPI application: REST API plus the built React app on the same origin."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import mlbridge
from app.api import auth, investigations
from app.config import REPO_ROOT, get_settings
from app.db import init_db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
logger = logging.getLogger("deeptrace")

settings = get_settings()
DIST_DIR = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()

    status = mlbridge.weights_status()
    logger.info("weights dir: %s", status["weights_dir"])
    logger.info("checkpoints present: %s", ", ".join(status["present"]) or "none")
    logger.info("analysis mode: %s | storage: %s | database: %s",
                settings.analysis_mode, settings.storage_backend,
                settings.database_url.split("@")[-1])

    if settings.require_weights:
        # Fail loudly rather than silently serving _PRIORS (~0.10-0.15) as if they were
        # real predictions.
        mlbridge.assert_weights_present()

    if not (DIST_DIR / "index.html").exists():
        logger.warning("no frontend build at %s - the API runs, but '/' will 404; "
                       "run `npm run build` in frontend/ to include the UI", DIST_DIR)

    yield
    logger.info("shutting down")


app = FastAPI(title="DeepTrace API", version="1.0.0", lifespan=lifespan)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(investigations.router, prefix="/api/investigations", tags=["investigations"])


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    status = mlbridge.weights_status()
    return {
        "status": "ok",
        "analysis_mode": settings.analysis_mode,
        "storage_backend": settings.storage_backend,
        "weights_ok": not status["missing"],
        "missing_checkpoints": status["missing"],
    }


@app.exception_handler(Exception)
async def unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
    """The frontend reads `detail` from the body, so never return a bare 500."""
    logger.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── the built frontend, served from the same origin ───────────────────────────
# Same origin means no CORS configuration is needed anywhere.
if (DIST_DIR / "index.html").exists():
    if (DIST_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        candidate = DIST_DIR / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        # unknown paths are client-side routes (/dashboard, /results/INV-0007, ...)
        return FileResponse(DIST_DIR / "index.html")
