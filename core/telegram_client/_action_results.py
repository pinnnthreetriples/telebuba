"""Failure-classification ``ActionResult`` builders for the typed-action executor.

Split from ``_actions.py`` to keep that module under the aislop file-size
budget. One builder per outcome family: rate-limit (the differentiated flood
family), infrastructure (``unavailable``), and generic failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logging import log_event
from schemas.telegram_actions import ActionResult

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionStatus, TelegramAction


async def _flood_action_result(
    account_id: str,
    action: TelegramAction,
    *,
    status: ActionStatus,
    seconds: int | None,
) -> ActionResult:
    """Log a Telegram rate-limit event and build the matching ``ActionResult``.

    Covers the differentiated flood family — generic flood-wait, per-peer
    ``PEER_FLOOD`` (no duration), per-chat slow mode, and premium-gated waits —
    so callers can react per type instead of treating a moderation restriction
    as an ordinary failure.
    """
    await log_event(
        "WARNING",
        f"telegram_{action.action_type}_{status}",
        account_id=account_id,
        extra={"seconds": seconds},
    )
    return ActionResult(
        status=status,
        action_type=action.action_type,
        account_id=account_id,
        flood_wait_seconds=seconds,
    )


async def _unavailable_result(
    account_id: str,
    action: TelegramAction,
    exc: Exception,
) -> ActionResult:
    """Infrastructure failure (pool connect / socket / timeout) — not the caller's fault.

    Distinct from ``failed`` so the API layer maps it to 503 unavailable
    instead of billing an internal outage as a 400 client error.
    """
    await log_event(
        "WARNING",
        "telegram_action_unavailable",
        account_id=account_id,
        extra={
            "action_type": action.action_type,
            "error_type": type(exc).__name__,
            "message": str(exc),
        },
    )
    return ActionResult(
        status="unavailable",
        action_type=action.action_type,
        account_id=account_id,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )


async def _generic_error(account_id: str, action: TelegramAction, exc: Exception) -> ActionResult:
    # Stable-code wrappers chain the real reason (Pillow error + magic bytes) as __cause__.
    cause = str(exc.__cause__) if exc.__cause__ is not None else None
    await log_event(
        "ERROR",
        f"telegram_{action.action_type}_failed",
        account_id=account_id,
        extra={"error_type": type(exc).__name__, "message": str(exc), "cause": cause},
    )
    return ActionResult(
        status="failed",
        action_type=action.action_type,
        account_id=account_id,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
