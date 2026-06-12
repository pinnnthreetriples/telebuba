"""NiceGUI entrypoint."""

from __future__ import annotations

from nicegui import app, ui

from core.config import settings
from core.logging import setup_logging
from features.accounts import register_accounts_page
from features.logs import register_logs_page
from features.warming import register_warming_page
from services.warming import reconcile_warming_runtime, shutdown_warming_runtime


def main() -> None:
    setup_logging()
    register_accounts_page()
    register_warming_page()
    register_logs_page()

    # _RUNTIME (per-account warming loops) lives in process memory; after a
    # restart the DB may still show ``active``/``sleeping`` rows whose task is
    # gone. Reconcile on startup, cancel on shutdown.
    app.on_startup(reconcile_warming_runtime)
    app.on_shutdown(shutdown_warming_runtime)

    ui.run(title="Telebuba", port=settings.ui.port, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
