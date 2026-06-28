"""App entrypoint — serves the Telebuba design SPA as the frontend.

The UI is the design's own static single-page app, served verbatim from
``web/`` (``index.html`` + ``support.js``, the design-canvas runtime). The
previous NiceGUI page layer (``features/``) was removed in the redesign: the
design file is the source of truth, so it is shipped as-is rather than
re-implemented. The Python backend (``services`` / ``core``) still runs the
warming and neurocomment runtimes on startup; wiring the static UI to live
data is a deliberate follow-up.

We keep NiceGUI's ``ui.run`` only for its FastAPI ``app`` and startup/shutdown
lifecycle — no NiceGUI pages are registered; the two routes below serve the
static design.
"""

from __future__ import annotations

import subprocess  # nosec B404 — read-only git rev-parse, no user input.
from pathlib import Path

from nicegui import app, ui

from core.config import settings
from core.logging import log_event, setup_logging
from core.telegram_client import shutdown_telegram_pool
from services.neurocomment import (
    reconcile_neurocomment_on_startup,
    shutdown_neurocomment_on_shutdown,
)
from services.warming import reconcile_warming_runtime, shutdown_warming_runtime

_GIT_SHA_TIMEOUT_SECONDS = 2
_WEB_DIR = Path(__file__).resolve().parent / "web"


def _git_sha() -> str:
    """Resolve the current commit SHA, falling back to "unknown" off-tree.

    Read once at startup so the operator can verify which code is actually
    running just by checking the boot log — saves a "did my git pull take
    effect?" round of guessing.
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
            cwd=Path(__file__).resolve().parent,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


async def _log_app_started() -> None:
    """Stamp the boot with the live commit SHA, for restart verification."""
    await log_event("INFO", "app_started", extra={"git_sha": _git_sha()})


def _register_frontend() -> None:  # pragma: no cover
    """Serve the design SPA via NiceGUI's static-file API (no direct FastAPI dep).

    ``index.html`` is served at ``/`` and its runtime at ``/support.js`` (the
    HTML loads ``./support.js`` relative to ``/``). GSAP, Babel and the web
    fonts load from their CDNs exactly as the design declares them.
    """
    app.add_static_file(local_file=_WEB_DIR / "support.js", url_path="/support.js")
    app.add_static_file(local_file=_WEB_DIR / "index.html", url_path="/")


def main() -> None:
    setup_logging()
    _register_frontend()

    # _RUNTIME (per-account warming loops) lives in process memory; after a
    # restart the DB may still show ``active``/``sleeping`` rows whose task is
    # gone. Reconcile on startup, cancel on shutdown.
    app.on_startup(_log_app_started)
    app.on_startup(reconcile_warming_runtime)
    # Neurocomment runtime is event-driven (a listener + per-post tasks); like
    # warming it lives in process memory, so resume the persisted listener on
    # boot and tear it down on exit.
    app.on_startup(reconcile_neurocomment_on_startup)
    # Shutdown order matters: drain warming's in-flight Telegram calls FIRST
    # so they can finish on the pooled client, THEN tear the pool down. The
    # other way around blows up live ``execute(...)`` calls mid-handshake and
    # may corrupt the ``.session`` SQLite file.
    app.on_shutdown(shutdown_warming_runtime)
    app.on_shutdown(shutdown_neurocomment_on_shutdown)
    app.on_shutdown(shutdown_telegram_pool)

    ui.run(
        title="Telebuba",
        port=settings.ui.port,
        reload=False,
        reconnect_timeout=settings.ui.reconnect_timeout,
    )


if __name__ in {"__main__", "__mp_main__"}:
    main()
