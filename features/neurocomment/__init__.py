"""NiceGUI Neurocomment page (issue #119).

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.neurocomment`` (or ``core.db`` read) function, and re-renders. No
business logic here — the campaign/channel/account wiring, onboarding, runtime
start/stop and the board read model all live in ``services/``. Mirrors the
structure and styling of ``features/warming`` (board cards + per-channel rows +
per-account terminal log) without importing from it (no cross-feature imports).

The whole page is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually; the logic it calls is unit-tested.
"""

from __future__ import annotations

from nicegui import ui

from features.neurocomment._page import render_neurocomment_page

__all__ = ["register_neurocomment_page"]


def register_neurocomment_page() -> None:  # pragma: no cover
    @ui.page("/neurocomment", title="Telebuba — Нейрокомментинг")
    async def neurocomment_page() -> None:
        await render_neurocomment_page()
