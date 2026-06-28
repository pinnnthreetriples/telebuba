"""App entrypoint — FastAPI/uvicorn composition root.

The backend is a thin FastAPI JSON API (``api/``) over the existing ``services/``;
``main.py`` builds the app, runs the warming + neurocomment runtimes via the
FastAPI ``lifespan``, and serves the frontend SPA as static files. uvicorn runs
**single-worker**: the runtimes are in-process asyncio tasks, so a second worker
would duplicate Telegram work and race the SQLite DB.

Frontend serving is transitional: the React build (``frontend/dist``) is served
once it exists; until then the current verbatim design SPA in ``web/`` is served
so the UI never goes dark. Issue #173 removes ``web/`` at React parity.
"""

from __future__ import annotations

import subprocess  # nosec B404 — read-only git rev-parse, no user input.
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from api import create_app
from core.config import settings
from core.logging import log_event, setup_logging
from core.telegram_client import shutdown_telegram_pool
from services.neurocomment import (
    reconcile_neurocomment_on_startup,
    shutdown_neurocomment_on_shutdown,
)
from services.warming import reconcile_warming_runtime, shutdown_warming_runtime

_GIT_SHA_TIMEOUT_SECONDS = 2
_ROOT = Path(__file__).resolve().parent
_FRONTEND_DIST = _ROOT / "frontend" / "dist"
_WEB_DIR = _ROOT / "web"


def _git_sha() -> str:
    """Resolve the current commit SHA, falling back to "unknown" off-tree.

    Read once at startup so the operator can verify which code is actually
    running just by checking the boot log.
    """
    try:
        # Fixed argv, no shell, no user input — invoking git on PATH is the
        # standard SHA-resolution idiom in dev/CI environments. nosec covers
        # bandit's B603 (subprocess call) and B607 (partial executable path);
        # ruff's S607 (same warning as B607) goes on the argv line.
        result = subprocess.run(  # nosec B603 B607
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_GIT_SHA_TIMEOUT_SECONDS,
            cwd=_ROOT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


async def _log_app_started() -> None:
    """Stamp the boot with the live commit SHA, for restart verification."""
    await log_event("INFO", "app_started", extra={"git_sha": _git_sha()})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start/stop the in-process runtimes around the server's lifetime.

    Shutdown order matters: drain warming's in-flight Telegram calls FIRST so
    they finish on the pooled client, THEN tear the pool down — the other way
    blows up live ``execute(...)`` calls mid-handshake and may corrupt the
    ``.session`` SQLite file.
    """
    setup_logging()
    await _log_app_started()
    await reconcile_warming_runtime()
    await reconcile_neurocomment_on_startup()
    try:
        yield
    finally:
        await shutdown_warming_runtime()
        await shutdown_neurocomment_on_shutdown()
        await shutdown_telegram_pool()


def _static_root() -> Path | None:
    # ponytail: serve the React build once it exists; until then keep the current
    # verbatim web/ SPA alive. #173 removes web/, leaving frontend/dist.
    if _FRONTEND_DIST.is_dir():
        return _FRONTEND_DIST
    if _WEB_DIR.is_dir():
        return _WEB_DIR
    return None


def _mount_frontend(app: FastAPI) -> None:  # pragma: no cover
    """Serve the SPA: StaticFiles for built assets + a catch-all for index.html.

    Mounted AFTER the API routers, so ``/api/*`` always wins. The catch-all
    returns ``index.html`` for client-side routes; real files (``support.js``,
    ``assets/*``) are served directly.
    """
    root = _static_root()
    if root is None:
        return
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}")
    async def _spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = root / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(root / "index.html")


app = create_app(lifespan=lifespan)
_mount_frontend(app)


def main() -> None:  # pragma: no cover
    uvicorn.run(
        "main:app",
        host=settings.api.host,
        port=settings.api.port,
        workers=1,
        reload=False,
    )


if __name__ == "__main__":
    main()
