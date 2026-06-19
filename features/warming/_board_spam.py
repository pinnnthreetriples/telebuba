"""Spam badge + «проверить» refresh link for the warming card.

Extracted from ``_board`` to keep that module under the aislop file-length
cap. UI-thin per non-negotiable #1; excluded from coverage. The actual
text/colour/tooltip logic lives in ``_board_checks`` (pure helpers).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from features.warming._board_checks import (
    _spam_badge_classes,
    _spam_badge_label,
    _spam_notify_type,
    _spam_outcome_label,
    _spam_tooltip,
)
from services.spam_status import refresh_spam_status

if TYPE_CHECKING:
    from features.warming._board import _BoardContext
    from schemas.warming import WarmingAccountState


def render_spam_badge(
    ctx: _BoardContext,
    card: WarmingAccountState,
) -> None:  # pragma: no cover
    """Spam-status badge — always visible, always self-explaining.

    The badge text distinguishes probe-error / Telegram-review / never-probed;
    the tooltip carries the underlying ``spam_detail`` so the operator knows
    where to look next.
    """
    status = card.spam_status or "unknown"
    text = _spam_badge_label(status, card.spam_detail)
    cls = _spam_badge_classes(status, card.spam_detail)
    tooltip = _spam_tooltip(status, card.spam_detail)
    with ui.row().classes("w-full items-center gap-2"):
        badge = ui.label(text).classes(f"w-fit text-[11px] px-2 py-0.5 rounded {cls}")
        if tooltip:
            badge.tooltip(tooltip)
        _render_spam_refresh_link(ctx, card.account_id)


def _render_spam_refresh_link(
    ctx: _BoardContext,
    account_id: str,
) -> None:  # pragma: no cover
    """Inline «проверить» link that triggers a real @SpamBot probe.

    Debounced: an operator double-click otherwise launches two parallel
    probes; only the second toast survives. ``inflight`` + ``disable``
    serialise the request, ``loading`` shows the spinner.
    """
    inflight = {"busy": False}

    async def on_click() -> None:
        if inflight["busy"]:
            return
        inflight["busy"] = True
        button.props("loading disable")
        try:
            verdict = await refresh_spam_status(account_id, force=True)
        except Exception as exc:  # noqa: BLE001 — UI handler surfaces any failure
            ui.notify(f"Не удалось проверить: {exc}", type="negative", timeout=6000)
            inflight["busy"] = False
            button.props(remove="loading disable")
            ctx.refresh()
            return
        label = _spam_outcome_label(verdict.status, verdict.detail)
        ui.notify(
            f"Спам-статус: {label}",
            type=_spam_notify_type(verdict.status, verdict.detail),
            timeout=6000,
        )
        inflight["busy"] = False
        button.props(remove="loading disable")
        ctx.refresh()

    button = (
        ui.button("проверить", on_click=on_click)
        .props("flat dense no-caps")
        .classes("text-[11px] text-blue-600 px-1 py-0")
    )
