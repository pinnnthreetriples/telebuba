"""The re-check of an open appeal: an ordinary DECISION ask under the shared budget.

Owner decision D15: once the operator says an appeal was filed, the account is asked about again
at the end day of a dated limit (plus the per-appeal jitter) or ``appeal_recheck_hours`` after
filing for an open-ended one. There is no new kind of probe and no extra budget: the loop below
calls the gateway's ``ask`` as a DECISION, forced past the cache (a stale ``limited`` reading is
exactly what it wants to replace), and the gateway's rules decide whether @SpamBot is actually
asked now, later (``deferred``, with a ``retry_at`` the loop honours) or not at all. A real
``clean`` closes the appeal in the gateway's store path (``_appeal.close_on_clean``) and lifts
the block through ``services.spam_block_lift``, as for any other @SpamBot ``clean``.

An appeal is due once, not forever: it stops being due as soon as a real @SpamBot reading is
newer than its due time, whatever that reading says. A block that is still there is then
rechecked by the ordinary mechanisms (``limited_recheck_days``, warming, the operator's button).

The loop only asks the gateway. It does not touch the web window and sends nothing itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.spam_appeals import list_open_appeals
from schemas.spam_status import is_unasked
from services.spam_intel import _gateway
from services.spam_intel._appeal import open_appeal, recheck_at
from services.spam_intel._evidence import verdicts_for
from services.spam_intel._policy import parse_moment

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

# The label the ask is recorded under in events and the ledger.
CALLER = "spam_intel.appeal_recheck"


class _State:
    task: asyncio.Task[None] | None = None
    # account -> do not ask again before this instant (the gateway said "later" or "no").
    held: dict[str, datetime] = {}  # noqa: RUF012 - one process-wide registry, reset by tests


_state = _State()


async def _sleep(seconds: float) -> None:
    """Sleep seam — patched to a no-op in tests."""
    await asyncio.sleep(seconds)


async def due_appeals(at: datetime) -> list[str]:
    """Accounts whose open appeal is due a re-check at ``at`` and not yet answered since."""
    cfg = settings.spam_intel
    appeals = await list_open_appeals()
    verdicts = await verdicts_for([a.account_id for a in appeals])
    due: list[str] = []
    for appeal in appeals:
        verdict = verdicts[appeal.account_id]
        if verdict.status == "frozen":
            continue
        when = recheck_at(appeal, verdict, cfg)
        answered = verdict.last_spambot_at is not None and verdict.last_spambot_at >= when
        if at >= when and not answered and await open_appeal(appeal.account_id) is not None:
            due.append(appeal.account_id)
    return due


def _held_until(verdict_retry: datetime | None, at: datetime) -> datetime:
    """A refused or postponed account is not asked again before its ``retry_at``, else later."""
    fallback = at + timedelta(hours=settings.spam_intel.appeal_recheck_hours)
    return verdict_retry or fallback


async def run_appeal_rechecks() -> int:
    """Ask the gateway about every due appeal once; the number of accounts asked."""
    at = _gateway.now()
    asked = 0
    for account_id in await due_appeals(at):
        held = _state.held.get(account_id)
        if held is not None and at < held:
            continue
        verdict = await _gateway.ask(account_id, force=True, purpose="decision", caller=CALLER)
        asked += 1
        if is_unasked(verdict):
            _state.held[account_id] = _held_until(parse_moment(verdict.retry_at), at)
        else:
            _state.held.pop(account_id, None)
    return asked


async def _run() -> None:
    interval = settings.spam_intel.appeal_scan_minutes * 60
    while True:
        await _sleep(interval)
        try:
            await run_appeal_rechecks()
        except Exception as exc:  # noqa: BLE001 - one bad pass must not end the loop.
            logger.warning("spam appeal recheck failed: %s", type(exc).__name__)


async def start_appeal_recheck() -> None:
    """Start the loop (lifespan startup); a no-op while the gateway is off."""
    if not settings.spam_intel.enabled or _state.task is not None:
        return
    _state.task = asyncio.create_task(_run(), name="spam-appeal-recheck")


async def shutdown_appeal_recheck() -> None:
    """Cancel the loop and wait for it to unwind."""
    task, _state.task = _state.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
