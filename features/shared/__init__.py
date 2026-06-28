"""Shared UI chrome reused across feature pages.

`features/shared/` is the single sanctioned exception to the no-cross-feature
rule (conventions #1): it holds cross-page presentation — the global top
navigation bar, the page shell and the design-system theme — so that chrome
lives in one place instead of being copy-pasted into every page header. It owns
no business logic and imports no feature.
"""

from __future__ import annotations

from features.shared.nav import (
    NAV_LINKS,
    nav_link_html,
    page_shell,
    render_nav,
)
from features.shared.theme import PRIMARY, apply_theme

__all__ = [
    "NAV_LINKS",
    "PRIMARY",
    "apply_theme",
    "nav_link_html",
    "page_shell",
    "render_nav",
]
