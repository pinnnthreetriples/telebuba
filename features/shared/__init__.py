"""Shared UI chrome reused across feature pages.

`features/shared/` is the single sanctioned exception to the no-cross-feature
rule (conventions #1): it holds cross-page presentation — currently the top
navigation bar — so that chrome lives in one place instead of being copy-pasted
into every page header. It owns no business logic and imports no feature.
"""

from __future__ import annotations

from features.shared.nav import TOP_BAR_CLASSES, render_nav

__all__ = ["TOP_BAR_CLASSES", "render_nav"]
