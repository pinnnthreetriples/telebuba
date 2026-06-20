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
    where to look next. The refresh action is collapsed into the badge itself:
    when status is unknown, the badge becomes a clickable label that triggers
    a real @SpamBot probe (debounced via ``inflight``).
    """
    status = card.spam_status or "unknown"
    text = _spam_badge_label(status, card.spam_detail)
    cls = _spam_badge_classes(status, card.spam_detail)
    tooltip = _spam_tooltip(status, card.spam_detail)
    inflight = {"busy": False}

    async def on_click() -> None:
        if inflight["busy"]:
            return
        inflight["busy"] = True
        try:
            verdict = await refresh_spam_status(card.account_id, force=True)
        except Exception as exc:  # noqa: BLE001 — UI handler surfaces any failure
            ui.notify(f"Не удалось проверить: {exc}", type="negative", timeout=6000)
            inflight["busy"] = False
            ctx.refresh()
            return
        label = _spam_outcome_label(verdict.status, verdict.detail)
        ui.notify(
            f"Спам-статус: {label}",
            type=_spam_notify_type(verdict.status, verdict.detail),
            timeout=6000,
        )
        inflight["busy"] = False
        ctx.refresh()

    with ui.row().classes("w-full items-center gap-2"):
        if status in ("clean", "limited"):
            # Spam status known — compact result badge.
            badge = ui.label(text).classes(
                f"text-[10px] text-slate-500 bg-slate-50 border border-slate-200 "
                f"px-2.5 py-0.5 rounded whitespace-nowrap {cls}"
            )
            if tooltip:
                badge.tooltip(tooltip)
        else:
            # Not yet probed — clickable label that triggers a real probe.
            ui.label("Проверить спам").classes(
                "text-[10px] text-blue-600 bg-blue-50 border border-blue-200 "
                "px-2.5 py-0.5 rounded cursor-pointer whitespace-nowrap"
            ).on("click", on_click)
