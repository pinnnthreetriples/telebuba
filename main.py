"""NiceGUI entrypoint."""

from __future__ import annotations

from nicegui import ui

from core.config import settings
from core.logging import setup_logging
from features.accounts import register_accounts_page


def main() -> None:
    setup_logging()
    register_accounts_page()
    ui.run(title="Telebuba", port=settings.ui.port, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
