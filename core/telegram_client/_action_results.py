"""Failure-classification ``ActionResult`` builders for the typed-action executor.

Split from ``_actions.py`` to keep that module under the aislop file-size
budget. One builder per outcome family: rate-limit (the differentiated flood
family), infrastructure (``unavailable``), and generic failure.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.logging import log_event
from core.telegram_client._util import event_name
from schemas.telegram_actions import ActionResult

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionStatus, TelegramAction

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
# ``exc`` arrives here from ``execute``'s ladder, so the traceback is passed
# explicitly rather than read from the ambient handler.
logger = logging.getLogger(__name__)

# ``ActionResult.error_type`` for the half of ``unavailable`` where the request was
# ALREADY on the wire: only the answer was lost, so Telegram may well have applied the
# action. The other half — the pool never handed back a client — keeps the raw exception
# class, because only that one PROVES nothing left this process. The classes cannot tell
# them apart (a socket dies the same way at either end), so the gateway, which alone knows
# WHICH call raised, spends ``error_type`` on the distinction, exactly as the frozen /
# dead-session ladders spend it on theirs. Re-exported from ``core.telegram_client``
# because it is a term of the ``ActionResult`` contract, not a gateway internal: any
# caller whose action is unsafe to repeat has to branch on it.
UNCONFIRMED_ERROR_TYPE = "UnconfirmedRequest"


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


async def _flood_action_result(  # noqa: PLR0913 - four keyword-only outcome facets, no bag
    account_id: str,
    action: TelegramAction,
    *,
    status: ActionStatus,
    seconds: int | None,
    applied_privacy_keys: list[str] | None = None,
    domain: str | None = None,
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
        event_name(domain, f"telegram_{action.action_type}_{status}"),
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
    *,
    dispatched: bool,
    domain: str | None = None,
) -> ActionResult:
    """Infrastructure failure (pool connect / socket / timeout) — not the caller's fault.

    Distinct from ``failed`` so the API layer maps it to 503 unavailable
    instead of billing an internal outage as a 400 client error.

    ``dispatched`` splits the family by what it PROVES, and is the caller's only way
    to learn it: ``False`` means the pool never handed back a client, so the request
    never left this process and repeating the action is free; ``True`` means it was
    already on the wire and the fault took the ANSWER, so Telegram may have applied it
    and a caller that must not act twice has to treat it as done. That half reports
    ``UNCONFIRMED_ERROR_TYPE`` rather than the exception class, because the classes are
    identical on both sides of the line. Nothing is lost — the real exception goes to
    stderr with its full traceback just below.
    """
    error_type = UNCONFIRMED_ERROR_TYPE if dispatched else type(exc).__name__
    logger.warning(
        "action %s unavailable for %s",
        action.action_type,
        account_id,
        exc_info=exc,
    )
    await log_event(
        "WARNING",
        # A fixed name, but still an action outcome, so it carries the domain
        # prefix like the composed ones; the SPA strips the prefix and resolves
        # the bare ``logEvent.telegram_action_unavailable`` label.
        event_name(domain, "telegram_action_unavailable"),
        account_id=account_id,
        extra={
            "action_type": action.action_type,
            "error_type": error_type,
        },
    )
    return ActionResult(
        status="unavailable",
        action_type=action.action_type,
        account_id=account_id,
        applied_privacy_keys=_applied_privacy_keys(exc),
        error_type=error_type,
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


async def _generic_error(
    account_id: str,
    action: TelegramAction,
    exc: Exception,
    *,
    domain: str | None = None,
) -> ActionResult:
    # A partially-completed channel_create carries the already-created id
    # (ChannelGatewayError.channel_id) so the caller can adopt the private
    # channel instead of re-creating a duplicate.
    created_id = getattr(exc, "channel_id", None)
    # Stable-code wrappers chain the real reason (Pillow error + magic bytes) as
    # __cause__, and unmapped Telethon errors are prose all the way down — both are
    # third-party text, so the whole chain goes to stderr and not into ``extra``.
    # Nothing is lost above: the stable code still rides ``error_message`` below into
    # ``AccountActionError`` and the HTTP envelope, which is where the SPA reads it.
    logger.error(
        "action %s failed for %s",
        action.action_type,
        account_id,
        exc_info=exc,
    )
    await log_event(
        "ERROR",
        event_name(domain, f"telegram_{action.action_type}_failed"),
        account_id=account_id,
        extra={"error_type": type(exc).__name__},
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


async def _join_by_request_result(
    account_id: str,
    action: TelegramAction,
    exc: Exception,
    *,
    domain: str | None = None,
) -> ActionResult:
    """A join request queued for admin approval — an expected outcome, logged at INFO.

    Not a failure: the group gates entry and our request is now pending. Only the log
    level differs from ``_generic_error`` — ERROR plus a stderr traceback for a normal
    outcome buried the real errors in the operator's log (28 in one afternoon), while
    the ``join_by_request`` state the domain derives was never logged at all. The
    ``failed`` status and ``error_type`` are preserved because that is what the domain
    keys its state off.
    """
    await log_event(
        "INFO",
        event_name(domain, f"telegram_{action.action_type}_by_request"),
        account_id=account_id,
        extra={"channel": getattr(action, "channel", None)},
    )
    return ActionResult(
        status="failed",
        action_type=action.action_type,
        account_id=account_id,
        error_type=type(exc).__name__,
        error_message=str(exc),
    )
