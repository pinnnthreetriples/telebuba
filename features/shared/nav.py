"""Top navigation bar shared by every feature page.

`NAV_LINKS` is the single source of truth for the cross-page menu: add a page
here once and its link appears in every header. Previously each page hand-rolled
its own nav row, so a newly added page (neurocomment) was reachable by URL but
missing from three of the four headers.
"""

from __future__ import annotations

from nicegui import ui

# (label, route) in display order — the only place the nav is defined.
NAV_LINKS: tuple[tuple[str, str], ...] = (
    ("Аккаунты", "/"),
    ("Прогрев", "/warming"),
    ("Нейрокомментинг", "/neurocomment"),
    ("Логи", "/logs"),
)

# The full-width white top bar every page wraps its header in (was duplicated
# verbatim across all four page headers).
TOP_BAR_CLASSES = (
    "w-full items-center justify-between px-4 py-2 bg-white "
    "text-slate-950 border-b border-slate-200"
)

_ACTIVE_CLASSES = "text-sm font-medium text-slate-900 no-underline"
_INACTIVE_CLASSES = "text-sm text-slate-600 hover:text-slate-900 no-underline"


def nav_link_classes(route: str, active: str) -> str:
    """Tailwind classes for one nav link — emphasised when it is the active route."""
    return _ACTIVE_CLASSES if route == active else _INACTIVE_CLASSES


def render_nav(active: str) -> None:  # pragma: no cover
    """Render the brand label + cross-page links, highlighting the `active` route."""
    with ui.row().classes("items-center gap-4"):
        ui.label("Telebuba").classes("text-lg font-semibold")
        for label, route in NAV_LINKS:
            ui.link(label, route).classes(nav_link_classes(route, active))
