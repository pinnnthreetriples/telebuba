"""NiceGUI Settings page (Настройки).

New in the redesign (``Telebuba.dc.html`` §C.6). The screen is a faithful
visual reproduction of the design's settings form — API key, warming &
neurocomment limits, and behaviour toggles. Like the design prototype it is a
client-side mock: the eye-toggle, switches and the "Сохранено" feedback run as
inline handlers in ``settings.html``, and **Save does not persist**. Wiring the
fields to live ``core.config`` values and a persistence layer is a separate
feature; the page is intentionally static markup so it stays UI-thin.

ponytail: the masked Gemini key shown is the design's placeholder, never the
real ``settings.gemini.api_key`` — rendering the live secret into the DOM (the
eye-toggle would reveal it) would leak a credential.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

from features.shared import page_shell

_SETTINGS_HTML = (Path(__file__).with_name("settings.html")).read_text(encoding="utf-8")


def register_settings_page() -> None:  # pragma: no cover
    @ui.page("/settings", title="Telebuba — Настройки")
    def settings_page() -> None:
        with page_shell("/settings"):
            ui.html(_SETTINGS_HTML)
