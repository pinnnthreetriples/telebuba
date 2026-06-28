"""Global design system — the visual foundation every page inherits.

Ported verbatim from the `Telebuba.dc.html` design (its single ``<style>``
block lives next to this module as ``theme.css``, plus the Inter / JetBrains
Mono web fonts it loads). Previously the app had no global theme: each page
hand-set ``bg-slate-50`` on the body and relied on Quasar defaults (indigo
primary). The redesign centralises that here — warm ``#F1EFED`` canvas, Inter
UI type, JetBrains Mono for phones/hosts/logs, ``#0066FF`` accent, and the
shared ``tb-*`` animation/utility classes.

The CSS lives in a sibling ``.css`` file rather than an inline string so the
design's minified rules survive verbatim (an inline string would trip ruff's
line-length gate). `ui.add_css` / `ui.add_head_html` with ``shared=True``
register once for every page — the same import-time pattern the warming and
neurocomment features use. `apply_theme()` is the per-page hook for the Quasar
palette, which must run inside a page render.
"""

from __future__ import annotations

from pathlib import Path

from nicegui import ui

# Blue accent — buttons, focus rings, live indicators. The one colour
# referenced from Python; the rest of the palette lives in theme.css.
PRIMARY = "#0066FF"

# Inter (UI) + JetBrains Mono (phones, hosts, times, logs) — exactly the two
# families the design loads from Google Fonts.
_FONTS_HTML = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Inter:wght@400;500;600;700&"
    'family=JetBrains+Mono:wght@400;500&display=swap">'
)

_HERE = Path(__file__).parent
# theme.css = the design's global canvas + animation/utility classes (verbatim).
# components.css = the reusable component vocabulary (buttons, cards, badges,
# segmented controls, inputs, stat cards, terminal, toggles) from spec §E.
_GLOBAL_CSS = (_HERE / "theme.css").read_text(encoding="utf-8")
_COMPONENTS_CSS = (_HERE / "components.css").read_text(encoding="utf-8")

ui.add_head_html(_FONTS_HTML, shared=True)
ui.add_css(_GLOBAL_CSS, shared=True)
ui.add_css(_COMPONENTS_CSS, shared=True)


def apply_theme() -> None:
    """Per-page hook: set the Quasar palette to the design's blue accent.

    The warm canvas and Inter type come from ``theme.css`` (applied to every
    page); only ``ui.colors`` must run inside a page render, so it lives here.
    Call once at the top of each page before rendering the nav.
    """
    ui.colors(primary=PRIMARY)
