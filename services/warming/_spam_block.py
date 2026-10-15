"""The confirmed spam block: the stop, the operator's «Оставить», and what it changes.

A spam block (@SpamBot's ``limited``: the account cannot write to people who do not
have its number) lasts as long as Telegram keeps it. Warming asks @SpamBot again once
after a quarantine rest; an account still limited then stops in ``spam_blocked`` — not a
generic error — and waits for the operator: «Удалить» (the ordinary account delete) or
«Оставить».

Keeping it puts the account on the warmed card (idle, graduated, ``spam_blocked_at``
kept), where both of its ways on are: the hand-off to neurocomment and «Вернуть в
прогрев» (a new wave). The graduation is a placement only: no ``promoted`` row is
journalled, so the survival counter sees no release.

While the mark stands the block is known, not news: DMs stay shut, a ``limited``
answer neither quarantines it nor refuses its start or hand-off, and the card wears a
«Спамблок» badge. A block is "known" from the stop itself, so even the parked account
never re-enters the quarantine ladder. Searching, joining, reading and reacting are not
touched: a spam block only shuts messages to strangers.

The first ``clean`` answer after the mark lifts it (``services.spam_block_lift``): the
account is back to normal, and a parked one goes idle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.db import mark_promoted_to_nc
from core.logging import log_event
from schemas.warming import WarmingCycleResult, spam_block_known
from services.warming._state import _set_state
from services.warming._waves import record_wave_end

if TYPE_CHECKING:
    from schemas.warming import WarmingStateRecord

_STALE = "stale run"


class NotSpamBlockedError(ValueError):
    """«Оставить» on an account warming never stopped for a spam block."""


async def stop_spam_blocked(
    record: WarmingStateRecord,
    now: datetime,
    checks: int,
    run_id: str | None,
) -> WarmingCycleResult:
    """Stop warming: @SpamBot still says ``limited`` after the quarantine re-check(s)."""
    account_id = record.account_id
    write = await _set_state(
        account_id,
        "spam_blocked",
        last_event="spam_blocked",
        last_error=None,
        heartbeat_at=now.isoformat(),
        stopped_at=now.isoformat(),
        quarantine_count=checks,
        spam_blocked_at=now.isoformat(),
        expected_run_id=run_id,
    )
    if run_id is not None and not write.applied:
        return WarmingCycleResult(account_id=account_id, status="skipped", detail=_STALE)
    await record_wave_end(record, "spam_blocked")
    await log_event(
        "WARNING",
        "warming_spam_blocked",
        account_id=account_id,
        extra={"checks": checks},
    )
    return WarmingCycleResult(account_id=account_id, status="error", detail="spam blocked")


async def keep_spam_blocked(record: WarmingStateRecord | None, account_id: str) -> None:
    """«Оставить»: off the problem list onto the warmed card, the block remembered.

    Run under the caller's per-account lock with ``record`` read inside it. Idempotent
    for an account already kept; refuses one that never stopped for a spam block. The
    lift (``services.spam_block_lift``) does not take that lock, so the placement is a
    compare-and-set on the parked row's generation, which the lift clears: a block lifted
    in between is refused here too, rather than re-marked.
    """
    if record is None or not spam_block_known(record):
        raise NotSpamBlockedError(account_id)
    if record.state != "spam_blocked":
        # A repeat «Оставить» only finishes a keep whose placement write did not land;
        # an account already warming again (or handed on) is never touched.
        unfinished = record.state == "idle" and record.last_event == "spam_block_kept"
        if unfinished and not record.promoted_to_nc:
            await mark_promoted_to_nc(account_id)
        return
    now = datetime.now(UTC).isoformat()
    write = await _set_state(
        account_id,
        "idle",
        last_event="spam_block_kept",
        last_error=None,
        run_id=None,
        spam_blocked_at=record.spam_blocked_at or now,
        expected_run_id=record.run_id,
        current=record,
    )
    if not write.applied:
        raise NotSpamBlockedError(account_id)
    await mark_promoted_to_nc(account_id)
    await log_event("INFO", "warming_spam_block_kept", account_id=account_id)
