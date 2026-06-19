"""Drag-drop handlers + per-card refreshable seeding for the warming board.

Extracted from ``_board`` so the rendering module stays under the aislop
file-length cap. UI-thin per non-negotiable #1; excluded from coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from core.logging import log_event
from features.warming._board_checks import _ru_reason
from schemas.warming import StartWarmingRequest, StopWarmingRequest
from services.warming import WarmingNotReadyError, start_warming, stop_warming

if TYPE_CHECKING:
    from collections.abc import Callable

    from features.warming._board import _BoardContext
    from schemas.warming import WarmingAccountState

    _RenderCard = Callable[[_BoardContext, WarmingAccountState], None]


async def drop_into_warming(
    ctx: _BoardContext,
    account_id: str,
) -> None:  # pragma: no cover
    """Handle a drop into the «Прогрев» column with explicit success/error toast."""
    label = ctx.card_store.get(account_id)
    name = label.label if label is not None else account_id
    try:
        await start_warming(StartWarmingRequest(account_id=account_id))
    except WarmingNotReadyError as exc:
        reasons = "; ".join(_ru_reason(reason) for reason in exc.reasons)
        ui.notify(f"Нельзя запустить: {reasons}", type="negative")
        return
    except Exception as exc:  # noqa: BLE001 — UI handler surfaces any failure
        await log_event(
            "ERROR",
            "warming_start_failed_unexpected",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        ui.notify(
            f"Не удалось запустить «{name}»: {type(exc).__name__}: {exc}",
            type="negative",
            timeout=6000,
        )
        return
    ui.notify(f"«{name}»: прогрев запущен", type="positive")


async def drop_into_idle(ctx: _BoardContext, account_id: str) -> None:  # pragma: no cover
    """Handle a drop into the «Простой» column — stop warming."""
    label = ctx.card_store.get(account_id)
    name = label.label if label is not None else account_id
    try:
        await stop_warming(StopWarmingRequest(account_id=account_id))
    except Exception as exc:  # noqa: BLE001 — UI handler surfaces any failure
        await log_event(
            "ERROR",
            "warming_stop_failed_unexpected",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        ui.notify(
            f"Не удалось остановить «{name}»: {type(exc).__name__}: {exc}",
            type="negative",
            timeout=6000,
        )
        return
    ui.notify(f"«{name}»: прогрев остановлен", type="positive")


def seed_card_refreshable(
    ctx: _BoardContext,
    card: WarmingAccountState,
    render_card: _RenderCard,
) -> None:  # pragma: no cover
    """Wrap a card render in its own ``@ui.refreshable`` for per-card dispatch.

    Each call defines a fresh refreshable bound to one account_id; the
    closure reads from ``ctx.card_store`` so the poll callback can drop a
    new ``WarmingAccountState`` in and call ``card_refresh[id].refresh()``
    to update *only* that card's subtree. ``render_card`` is injected by
    the caller so this helper does not need to import the renderer (which
    would create a cycle with ``_board``).
    """
    account_id = card.account_id
    ctx.card_store[account_id] = card

    @ui.refreshable
    def render_one(card_id: str = account_id) -> None:
        current = ctx.card_store.get(card_id)
        if current is None:
            return
        render_card(ctx, current)

    render_one()
    ctx.card_refresh[account_id] = render_one
