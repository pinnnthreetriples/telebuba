"""App entrypoint — FastAPI/uvicorn composition root.

The backend is a thin FastAPI JSON API (``api/``) over the existing ``services/``;
``main.py`` builds the app, runs the warming + neurocomment runtimes via the
FastAPI ``lifespan``, and serves the built React SPA (``frontend/dist``) as
static files. uvicorn runs **single-worker**: the runtimes are in-process
asyncio tasks, so a second worker would duplicate Telegram work and race the
SQLite DB.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess  # nosec B404 — read-only git rev-parse, no user input.
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from api import create_app
from core.config import settings
from core.db import run_db_maintenance_loop
from core.gemini import close_gemini_client
from core.logging import log_event, setup_logging
from core.openai import close_openai_client
from core.telegram_client import shutdown_telegram_pool
from services.auth import seed_admin_if_empty
from services.neurocomment import (
    reconcile_neurocomment_on_startup,
    shutdown_neurocomment_on_shutdown,
)
from services.warming import reconcile_warming_runtime, shutdown_warming_runtime

_GIT_SHA_TIMEOUT_SECONDS = 2
_ROOT = Path(__file__).resolve().parent
_FRONTEND_DIST = _ROOT / "frontend" / "dist"


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
    await seed_admin_if_empty()
    # DB is initialised by the seed above; start the periodic SQLite maintenance
    # (WAL checkpoint + optional backup) alongside — separate from the warming /
    # neurocomment / pool lifecycle, so their start/stop order is untouched.
    maintenance_task = asyncio.create_task(run_db_maintenance_loop())
    await reconcile_warming_runtime()
    await reconcile_neurocomment_on_startup()
    try:
        yield
    finally:
        await shutdown_warming_runtime()
        await shutdown_neurocomment_on_shutdown()
        await shutdown_telegram_pool()
        maintenance_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance_task
        await close_gemini_client()
        await close_openai_client()


def _safe_spa_file(path: str) -> Path | None:
    """Resolve ``path`` to a real file inside ``frontend/dist``, or ``None``.

    Guards the SPA catch-all against path traversal: an attacker requesting
    ``../../.env`` (or a backslash/encoded/absolute variant) must never escape
    ``frontend/dist`` and serve ``.env`` / ``telebuba.db`` / ``sessions/*``.
    Rejects any ``..`` segment and absolute inputs up front, then requires the
    resolved candidate to stay under the resolved dist root. On any failure the
    caller falls back to serving ``index.html`` (SPA fallback), never the file.
    """
    if not path:
        return None
    # Normalise separators so a Windows-style ``..\\..`` is caught too, then
    # reject any parent-dir segment or an absolute path before touching disk.
    parts = path.replace("\\", "/").split("/")
    if ".." in parts or PurePosixPath(path).is_absolute() or Path(path).is_absolute():
        return None
    root = _FRONTEND_DIST.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _mount_frontend(app: FastAPI) -> None:  # pragma: no cover
    """Serve the React build: StaticFiles for assets + a catch-all for index.html.

    Mounted AFTER the API routers, so ``/api/*`` always wins. The catch-all
    returns ``index.html`` for client-side routes; real files (hashed
    ``assets/*``) are served directly. No-ops until ``frontend/dist`` exists.
    """
    if not _FRONTEND_DIST.is_dir():
        return
    assets = _FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}")
    async def _spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        safe = _safe_spa_file(path)
        if safe is not None:
            return FileResponse(safe)
        return FileResponse(_FRONTEND_DIST / "index.html")


app = create_app(lifespan=lifespan)
_mount_frontend(app)


def main() -> None:  # pragma: no cover
    uvicorn.run(
        "main:app",
        host=settings.api.host,
        port=settings.api.port,
        workers=1,
        reload=False,
        # Trust X-Forwarded-For only from configured upstreams so
        # ``request.client.host`` (the rate-limiter key) reflects the real client
        # behind a reverse proxy. Off by default — never trust a spoofable header
        # on a direct-exposed deploy.
        proxy_headers=settings.api.trust_proxy_headers,
        forwarded_allow_ips=settings.api.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
