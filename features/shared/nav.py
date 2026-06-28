"""Top navigation bar + page shell shared by every feature page.

`NAV_LINKS` is the single source of truth for the cross-page menu: add a page
here once and its link appears in every header. The redesign turns the top bar
into pure global chrome — logo, the five nav links, the "Система активна"
status badge, a notification bell and the operator avatar — matching
``Telebuba.dc.html`` section B. Page-specific actions (``+ Аккаунт``,
``Остановить пул`` …) now live in each page's own H1 row, not up here.

`page_shell(active)` is the one wrapper every page opens: it applies the theme,
renders this header, and yields the centred ``<main>`` content column
(``max-width:1340px``) the design specifies.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from nicegui import ui

from features.shared.theme import apply_theme

if TYPE_CHECKING:
    from collections.abc import Iterator

# (label, route) in display order — the only place the nav is defined.
NAV_LINKS: tuple[tuple[str, str], ...] = (
    ("Аккаунты", "/"),
    ("Прогрев", "/warming"),
    ("Нейрокомментинг", "/neurocomment"),
    ("Логи", "/logs"),
    ("Настройки", "/settings"),
)

# Logo: 26×26 ink square with a centred 9×9 blue dot, then the wordmark.
_LOGO_HTML = (
    '<div style="display:flex;align-items:center;gap:9px;flex-shrink:0">'
    '<div style="width:26px;height:26px;border-radius:8px;background:#0B0B0C;'
    'display:flex;align-items:center;justify-content:center">'
    '<div style="width:9px;height:9px;border-radius:50%;background:#0066FF"></div></div>'
    '<span style="font-size:15px;font-weight:700;letter-spacing:-.01em;'
    'color:#0B0B0C">Telebuba</span></div>'
)

# Right cluster: green system-status badge, notification bell (unread dot),
# operator avatar. Decorative — no behaviour is wired in the design.
_RIGHT_HTML = (
    '<div style="display:flex;align-items:center;gap:10px;flex-shrink:0">'
    '<div style="display:inline-flex;align-items:center;gap:6px;padding:5px 11px;'
    'border-radius:9999px;background:#DDF7E9">'
    '<span style="width:7px;height:7px;border-radius:50%;background:#16B364"></span>'
    '<span style="font-size:12px;font-weight:500;color:#12A150">Система активна</span></div>'
    '<button style="position:relative;width:34px;height:34px;border-radius:9999px;'
    "border:1px solid #E6E5E3;background:#fff;color:#74726E;display:flex;"
    'align-items:center;justify-content:center;cursor:pointer">'
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>'
    '<path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>'
    '<span style="position:absolute;top:7px;right:8px;width:6px;height:6px;'
    'border-radius:50%;background:#0066FF;border:1.5px solid #fff"></span></button>'
    '<div style="width:34px;height:34px;border-radius:9999px;background:#0066FF;'
    "color:#fff;font-size:13px;font-weight:600;display:flex;align-items:center;"
    'justify-content:center">ОП</div></div>'
)


def nav_link_html(label: str, route: str, active: str) -> str:
    """One nav link's HTML — bold + blue underline when it is the active route.

    Pure so the active-emphasis contract can be asserted without rendering.
    """
    is_active = route == active
    weight = "600" if is_active else "500"
    color = "#0B0B0C" if is_active else "#9A9893"
    underline = "#0066FF" if is_active else "transparent"
    return (
        f'<a href="{route}" class="tb-nav" style="text-decoration:none;'
        f"font-size:13px;padding:18px 2px 16px;font-weight:{weight};color:{color};"
        f'border-bottom:2px solid {underline};transition:color .2s">{label}</a>'
    )


def render_nav(active: str) -> None:  # pragma: no cover
    """Render the sticky global top bar, highlighting the `active` route."""
    links = "".join(nav_link_html(label, route, active) for label, route in NAV_LINKS)
    ui.html(
        '<header style="position:sticky;top:0;z-index:40;width:100%;'
        "background:rgba(255,255,255,.85);backdrop-filter:blur(10px);"
        '-webkit-backdrop-filter:blur(10px);border-bottom:1px solid #E6E5E3">'
        '<div style="max-width:1340px;margin:0 auto;padding:0 24px;height:56px;'
        'display:flex;align-items:center;gap:28px">'
        f"{_LOGO_HTML}"
        '<nav style="display:flex;align-items:center;gap:22px;flex:1">'
        f"{links}</nav>"
        f"{_RIGHT_HTML}"
        "</div></header>",
    )


@contextmanager
def page_shell(active: str) -> Iterator[None]:  # pragma: no cover
    """Open the standard page chrome: theme + sticky nav + centred ``<main>``.

    Strips NiceGUI's default content padding so the header is flush and the
    warm canvas reaches the edges; the yielded column owns the inner padding
    and the 1340px max width the design specifies.
    """
    apply_theme()
    ui.query(".nicegui-content").classes("!p-0 !gap-0")
    render_nav(active)
    with ui.column().style(
        "width:100%;max-width:1340px;margin:0 auto;padding:24px 24px 80px;gap:16px",
    ):
        yield
