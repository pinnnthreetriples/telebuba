"""The re-check of idle accounts: a ROUTINE ask under the shared budget (design 5.5, D5).

An account with a proxy that neither warming nor an open appeal covers would keep a stale verdict
for ever: ``needs_probe`` (an undated limit past ``limited_recheck_days``, a dated limit that
ended) was only looked at once someone asked. This pass is that someone: every
``SPAM_RECHECK__SCAN_MINUTES`` it picks ONE alive account with a proxy whose verdict, re-aged to
now, says ``recheck_due`` or ``limit_expired`` and asks the gateway about it as an ordinary
ROUTINE ask. There is no direct probe and no second budget: the gateway's rules (single flight,
backoff, per-proxy and fleet spacing, caps, the quiet start, the account's night) decide whether
@SpamBot is asked now, later or not at all, and an account it turned away is left alone until its
``retry_at`` (or ``SPAM_RECHECK__HOLD_MINUTES``) so the next pass reaches another account.

Only with ``passive_decisions`` on: ``needs_probe`` is an evidence concept the gateway acts on
only then (``_served``); off, the stored reading alone decides and the pass has nothing to add.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.accounts import list_accounts
from schemas.spam_status import is_unasked
from services.spam_intel import _gateway
from services.spam_intel._evidence import current_view, verdicts_for
from services.spam_intel._policy import parse_moment

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)

# The label the ask is recorded under in events and the ledger.
CALLER = "spam_intel.idle_recheck"
_REASONS = frozenset({"recheck_due", "limit_expired"})
# An idle account is alive: a "new" one has not been judged by a session check yet.
_ALIVE = "alive"


class _State:
    task: asyncio.Task[None] | None = None
    # account -> do not pick it again before this instant (the gateway said "later" or "no").
    held: dict[str, datetime] = {}  # noqa: RUF012 - one process-wide registry, reset by tests


_state = _State()


def active() -> bool:
    """Whether the pass may run at all: it acts on evidence, so it is a decision (see above)."""
    intel = settings.spam_intel
    return settings.spam_recheck.enabled and intel.enabled and intel.passive_decisions


async def _sleep(seconds: float) -> None:
    """Sleep seam: patched to a no-op in tests."""
    await asyncio.sleep(seconds)


async def due_accounts(at: datetime) -> list[str]:
    """Idle accounts due a re-check at ``at``, the longest-waiting first.

    Alive, with a proxy, not held back by an earlier "later". The stored verdict narrows the
    list (a limit, or a flag already set); each survivor is then folded afresh, because a stored
    ``recheck_due`` is only as old as its last fold and an undated limit ages in silence.
    """
    candidates = [
        account.account_id
        for account in (await list_accounts()).accounts
        if account.status == _ALIVE
        and account.proxy_id is not None
        and (_state.held.get(account.account_id) or at) <= at
    ]
    stored = await verdicts_for(candidates)
    due: list[tuple[datetime, str]] = []
    for account_id in candidates:
        first = stored[account_id]
        if first.status != "limited" and not first.needs_probe:
            continue
        view, _items = await current_view(account_id)
        if view.needs_probe and view.reason in _REASONS:
            due.append((view.primary_observed_at or at, account_id))
    return [account_id for _when, account_id in sorted(due)]


def _held_until(retry_at: datetime | None, at: datetime) -> datetime:
    return retry_at or at + timedelta(minutes=settings.spam_recheck.hold_minutes)


async def run_idle_recheck() -> str | None:
    """Ask the gateway about the most overdue idle account, once; its id, or ``None``."""
    if not active():
        return None
    at = _gateway.now()
    for account_id in await due_accounts(at):
        verdict = await _gateway.ask(account_id, purpose="routine", caller=CALLER)
        if is_unasked(verdict):
            _state.held[account_id] = _held_until(parse_moment(verdict.retry_at), at)
        else:
            _state.held.pop(account_id, None)
        return account_id
    return None


async def _run() -> None:
    while True:
        await _sleep(settings.spam_recheck.scan_minutes * 60)
        try:
            await run_idle_recheck()
        except Exception as exc:  # noqa: BLE001 - one bad pass must not end the loop.
            logger.warning("spam idle recheck failed: %s", type(exc).__name__)


async def start_idle_recheck() -> None:
    """Start the loop (lifespan startup); a no-op while it is off or already running."""
    if not active() or _state.task is not None:
        return
    _state.task = asyncio.create_task(_run(), name="spam-idle-recheck")


async def shutdown_idle_recheck() -> None:
    """Cancel the loop and wait for it to unwind."""
    task, _state.task = _state.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
