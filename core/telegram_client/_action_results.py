"""Failure-classification ``ActionResult`` builders for the typed-action executor.

Split from ``_actions.py`` to keep that module under the aislop file-size
budget. One builder per outcome family: rate-limit (the differentiated flood
family), infrastructure (``unavailable``), and generic failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.logging import log_event
from schemas.telegram_actions import ActionResult

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionStatus, TelegramAction


@dataclass(frozen=True)
class _DispatchResult:
    """One action's dispatch outcome.

    Carries the ``message_id`` (if any), the new ``channel_id`` (set only by
    ``channel_create``), plus dynamic log fields the static
    ``_action_log_extra`` can't know — e.g. the reaction emoji the gateway
    actually placed, chosen at dispatch time. Lives here (not ``_actions``)
    so the channel dispatcher can build one without a circular import.
    """

    message_id: int | None = None
    channel_id: int | None = None
    log_extra: dict[str, object] | None = None
    # Recent post ids fetched during a read, threaded to a following react so it
    # skips re-fetching the same channel (set only by ``read_channel``).
    recent_message_ids: list[int] | None = None


async def _flood_action_result(
    account_id: str,
    action: TelegramAction,
    *,
    status: ActionStatus,
    seconds: int | None,
    applied_privacy_keys: list[str] | None = None,
) -> ActionResult:
    """Log a Telegram rate-limit event and build the matching ``ActionResult``.

    Covers the differentiated flood family — generic flood-wait, per-peer
    ``PEER_FLOOD`` (no duration), per-chat slow mode, and premium-gated waits —
    so callers can react per type instead of treating a moderation restriction
    as an ordinary failure.

    ``applied_privacy_keys`` is threaded only by the caller that can produce a
    partial write (``set_privacy_settings`` hitting a flood on its second key).
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
        applied_privacy_keys=applied_privacy_keys,
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
        applied_privacy_keys=_applied_privacy_keys(exc),
        error_type=type(exc).__name__,
        error_message=str(exc),
    )


def _applied_privacy_keys(exc: BaseException) -> list[str] | None:
    """Privacy keys ``dispatch_set_privacy_settings`` applied before this refusal.

    Attached to the escaping exception by the gateway rather than returned, so the
    flood / frozen / dead-session ladders keep classifying the original error. Same
    ``getattr`` contract as ``ChannelGatewayError.channel_id`` below.

    The ``__cause__`` is checked too: those ladders hand us a stable-code wrapper
    (``ProfileGatewayError("account_frozen")``) whose cause is the annotated error.
    """
    for candidate in (exc, exc.__cause__):
        applied = getattr(candidate, "privacy_applied", None)
        if isinstance(applied, list) and applied:
            return [str(key) for key in applied]
    return None


async def _generic_error(account_id: str, action: TelegramAction, exc: Exception) -> ActionResult:
    # Stable-code wrappers chain the real reason (Pillow error + magic bytes) as __cause__.
    cause = str(exc.__cause__) if exc.__cause__ is not None else None
    # A partially-completed channel_create carries the already-created id
    # (ChannelGatewayError.channel_id) so the caller can adopt the private
    # channel instead of re-creating a duplicate.
    created_id = getattr(exc, "channel_id", None)
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
        channel_id=str(created_id) if isinstance(created_id, int) else None,
        applied_privacy_keys=_applied_privacy_keys(exc),
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
