"""NiceGUI entrypoint."""

from __future__ import annotations

import subprocess  # nosec B404 — read-only git rev-parse, no user input.
from pathlib import Path

from nicegui import app, ui

from core.config import settings
from core.logging import log_event, setup_logging
from core.telegram_client import shutdown_telegram_pool
from features.accounts import register_accounts_page
from features.accounts._profile_dialog_render import register_disconnect_tracker
from features.logs import register_logs_page
from features.neurocomment import register_neurocomment_page
from features.settings import register_settings_page
from features.warming import register_warming_page
from services.neurocomment import (
    reconcile_neurocomment_on_startup,
    shutdown_neurocomment_on_shutdown,
)
from services.warming import reconcile_warming_runtime, shutdown_warming_runtime

_GIT_SHA_TIMEOUT_SECONDS = 2


def _git_sha() -> str:
    """Resolve the current commit SHA, falling back to "unknown" off-tree.

    Read once at startup so the operator can verify which code is actually
    running just by checking the top of the Logs page — saves a "did my
    git pull take effect?" round of guessing.
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
    """Stamp the boot in the Logs page with the live commit SHA.

    Sole purpose: the operator sees ``app_started`` at the top of the logs
    after a restart with the actual SHA the process is running, and can
    compare against ``git rev-parse HEAD`` in two seconds. Solves the
    "is my pull actually live?" diagnostic that ate the previous session.
    """
    await log_event("INFO", "app_started", extra={"git_sha": _git_sha()})


def main() -> None:
    setup_logging()
    register_accounts_page()
    register_warming_page()
    register_neurocomment_page()
    register_logs_page()
    register_settings_page()
    # Populate _DEAD_CLIENTS on websocket drop so the profile dialog's apply
    # paths short-circuit on detached clients instead of warning to stderr.
    register_disconnect_tracker()

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
