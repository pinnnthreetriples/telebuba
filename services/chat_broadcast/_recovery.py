"""Self-recovery: the run goes on while any account can still come back, and says why it stalls.

Who can come back, and who cannot:

* An account ANOTHER FEATURE holds (warming, neurocomment, a listener...) is active in the
  roster but not held by this run. The ``claimer`` takes it as soon as it is free.
* An account halted for errors in a row, or whose worker crashed, is tried again by the
  ``retrier`` after a pause, a bounded number of times in a row; a send that goes out
  starts the count over.
* A spam block and a dead session (``FINAL_HALT_REASONS``) are never brought back here:
  only Start / Continue does that.

Both tasks exist only while there is something to wait for and end on their own (taking
an account, or finding nothing left to wait for), and neither is ever called from inside
the scheduler: taking an account waits for its lifecycle lock, which another feature may
hold for minutes.

A run that has no task at all and work left is the honest stall, and it names why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from core.config import settings
from core.logging import log_event
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _moves, _rotation, _seams, _send, _state

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import RosterAccount
    from services.chat_broadcast._context import RunContext

# Halts that a pause can undo: the account itself may be fine.
RETRYABLE_HALTS: Final = frozenset({"errors_in_a_row", "worker_crash"})
SPAM_HALT: Final = "spam_block"
# Calls in flight and requests waiting on an account are the account's, not orphans.
_KEPT_AS_THEY_ARE: Final = frozenset({"joining", "captcha", "pending_approval"})
# What a stalled run says about why, in ``campaign.last_error``.
STALL_SPAM: Final = "accounts_spam"
STALL_ERRORS: Final = "accounts_errors"
STALL_DEAD: Final = "accounts_dead"
# Nothing left but accounts another campaign or feature holds, and none came free in time.
STALL_BUSY: Final = "accounts_busy"


async def left_out(campaign_id: str) -> list[str]:
    """Roster accounts that are active but not held by this run: another feature has them."""
    return [
        item.account_id
        for item in await repository.list_roster(campaign_id)
        if item.state == "active" and not _rotation.held(item, campaign_id)
    ]


async def retryable(campaign_id: str) -> list[RosterAccount]:
    """Halted accounts a pause can bring back, with tries left."""
    limit = settings.chat_broadcast.halt_retry_max
    return [
        item
        for item in await repository.list_roster(campaign_id)
        if item.state == "halted"
        and item.halted_reason in RETRYABLE_HALTS
        and _state.retries(campaign_id, item.account_id) < limit
    ]


async def stall_cause(campaign_id: str) -> str:
    """Why nobody is left: a spam block first, then errors that ran out of tries, else dead."""
    reasons = {
        item.halted_reason
        for item in await repository.list_roster(campaign_id)
        if item.state == "halted"
    }
    if SPAM_HALT in reasons:
        return STALL_SPAM
    if reasons & RETRYABLE_HALTS:
        return STALL_ERRORS
    return STALL_DEAD


async def mark_waiting_account(ctx: RunContext) -> None:
    """Nobody is live: the chats in play wait for an account instead of looking assigned.

    A join request pending approval is not touched: it belongs to the account that sent it,
    and rewriting the chat would have the next account send a second request.
    """
    for target in await repository.list_targets(ctx.campaign_id):
        if target.state in _moves.ACTIVE_STATES and target.state not in _KEPT_AS_THEY_ARE:
            await repository.update_target(
                ctx.campaign_id,
                target.chat_key,
                expect_states=_moves.ACTIVE_STATES,
                state="waiting_account",
            )


async def watch_left_out(ctx: RunContext) -> bool:
    """Take back accounts other features release; ``True`` = took some."""
    from services.chat_broadcast import _runtime  # noqa: PLC0415 - import cycle

    limits = settings.chat_broadcast
    started = _seams.now()
    while True:
        _seams.assert_live_run()
        if not await left_out(ctx.campaign_id) or await _send.volume_reached(ctx):
            return False
        if _seams.now() - started >= limits.claim_give_up_seconds:
            # Nothing came free in all that time: the supervisor decides whether to go on.
            return False
        await _seams.sleep(limits.claim_poll_seconds)
        _seams.assert_live_run()
        taken = await _runtime.claim_free(ctx.campaign_id, await left_out(ctx.campaign_id))
        for account_id in taken:
            await log_event(
                "INFO",
                "chat_broadcast_account_back",
                account_id=account_id,
                extra={"campaign_id": ctx.campaign_id, "reason": "freed"},
            )
        if taken:
            return True


async def watch_halted(ctx: RunContext) -> bool:
    """Bring halted accounts back after their pause; ``True`` = revived some."""
    limits = settings.chat_broadcast
    while True:
        _seams.assert_live_run()
        candidates = await retryable(ctx.campaign_id)
        if not candidates or await _send.volume_reached(ctx):
            return False
        now = _seams.now()
        waits: dict[str, float] = {}
        for item in candidates:
            halted = _state.halted_at(ctx.campaign_id, item.account_id)
            if halted is None:
                # Halted before this process began: its pause counts from now.
                _state.note_halt(ctx.campaign_id, item.account_id, now)
                halted = now
            waits[item.account_id] = halted + limits.halt_retry_seconds - now
        ready = [account_id for account_id, wait in waits.items() if wait <= 0]
        if not ready:
            await _seams.sleep(min(limits.idle_poll_seconds, *waits.values()))
            continue
        for account_id in ready:
            await repository.revive_account(ctx.campaign_id, account_id)
            _state.note_retry(ctx.campaign_id, account_id)
            await log_event(
                "INFO",
                "chat_broadcast_account_back",
                account_id=account_id,
                extra={"campaign_id": ctx.campaign_id, "reason": "retry"},
            )
        return True
