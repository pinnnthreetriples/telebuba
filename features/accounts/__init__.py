"""NiceGUI accounts page.

UI-thin per non-negotiable #1. Each handler is a small pass-through to
``services.accounts``. The page is split into rendering modules:

- :mod:`._header`        — top toolbar + nav
- :mod:`._metrics`       — metric tile rendering
- :mod:`._table_section` — search controls + accounts table
- :mod:`._controller`    — event handlers / page state
- :mod:`._page`          — page composition root (route + wiring)
- :mod:`._dialogs`       — add / edit-profile / proxy dialogs
- :mod:`._table`         — column defs, cell templates, row + event helpers

This module is re-export only: ``main.py`` calls :func:`register_accounts_page`
from here.
"""

from __future__ import annotations

from features.accounts._page import register_accounts_page

__all__ = ["register_accounts_page"]
