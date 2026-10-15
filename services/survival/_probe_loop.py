"""Survival probes: ask @SpamBot on day 7 and day 30 after an account's release (3.3).

One background task, started in the lifespan, makes one pass an hour. A pass first
journals any death no session check recorded (an action found the account frozen), so
the counter sees it, then asks about at most one account (``services.survival._due``).
The ask is the same cached ``refresh_spam_status`` the warming cycle and the hand-off
use — no second Telegram path: a fresh cached answer is reused, a new one lands in the
journal as ``spambot_verdict``.
Attempts and the per-proxy spacing are remembered in memory, which is safe because one
uvicorn worker runs; after a restart the journal's own answers still space the asks.

A pass with no probe due re-asks about one account whose confirmed spam block is on
record and that nothing else is using (no warming loop, whose own cycle asks; no
feature's claim; no channel-search loan) once its last answer is
``block_recheck_hours`` old: the spam check lifts the block on a ``clean`` answer
(``services.spam_block_lift``). A stored ``clean`` newer than the block lifts it
without asking.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.accounts import list_accounts
from core.repositories.lifecycle import list_all_lifecycle_events
from core.repositories.warming import list_warming_states
from schemas.warming import is_parked, is_warming
from services._account_owner import is_lent, owner_of
from services.lifecycle_journal import DEAD_SESSION_STATUSES, record_session_death
from services.spam_block_lift import lift_from_stored_answer
from services.spam_status import refresh_spam_status
from services.survival._due import (
    DIRECT,
    BlockRecheckCandidate,
    ProbeCandidate,
    ProbePacing,
    pick_block_recheck,
    pick_due_probe,
)
from services.survival._judging import check_grace
from services.survival._outcomes import life_facts
from services.survival._stints import build_lives, parse_moment

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.lifecycle import LifecycleEvent
    from schemas.warming import WarmingStateRecord

# Stdlib sink for the traceback; the persisted event carries only the error type.
logger = logging.getLogger(__name__)

_SECONDS_PER_HOUR = 3600


@dataclass
class _State:
    task: asyncio.Task[None] | None = None
    # This process's last probe per account and per proxy.
    attempts: dict[str, datetime] = field(default_factory=dict)
    asked_by_proxy: dict[str, datetime] = field(default_factory=dict)


_state = _State()


def _pacing(now: datetime) -> ProbePacing:
    survival = settings.survival
    return ProbePacing(
        now=now,
        grace=check_grace(),
        spacing=timedelta(seconds=survival.probe_proxy_spacing_seconds),
        retry=timedelta(seconds=survival.probe_retry_hours * _SECONDS_PER_HOUR),
    )


async def _record_missed_deaths(accounts: list[AccountRead]) -> None:
    """Journal a death no session check saw: an action can mark an account ``frozen``."""
    for account in accounts:
        if account.status in DEAD_SESSION_STATUSES:
            await record_session_death(account, account.status)


@dataclass(frozen=True)
class _Fleet:
    """The living accounts' proxies, current-life rows and latest answers, read once."""

    proxies: dict[str, str]
    events: list[LifecycleEvent]
    # The latest @SpamBot answer per account and behind each proxy (journal + memory).
    answered: dict[str, datetime]
    asked_by_proxy: dict[str, datetime]


async def _fleet(everyone: list[AccountRead]) -> _Fleet:
    accounts = {
        account.account_id: account
        for account in everyone
        if account.status not in DEAD_SESSION_STATUSES
    }
    proxies = {account_id: account.proxy_id or DIRECT for account_id, account in accounts.items()}
    events = [
        event
        for event in await list_all_lifecycle_events(list(accounts))
        if event.created_at >= accounts[event.account_id].created_at
    ]
    answered: dict[str, datetime] = {}
    asked = dict(_state.asked_by_proxy)
    for event in events:
        moment = parse_moment(event.created_at)
        if event.kind != "spambot_verdict" or moment is None:
            continue
        answered[event.account_id] = max(answered.get(event.account_id, moment), moment)
        key = proxies[event.account_id]
        asked[key] = max(asked.get(key, moment), moment)
    return _Fleet(proxies, events, answered, asked)


def _candidates(fleet: _Fleet) -> list[ProbeCandidate]:
    """Living accounts' current lives."""
    return [
        ProbeCandidate(life.account_id, fleet.proxies[life.account_id], life_facts(life))
        for life in build_lives(fleet.events)
        if life.current
    ]


def _free_of_runtimes(record: WarmingStateRecord) -> bool:
    """No warming loop (it asks @SpamBot itself) and no other feature holds the account."""
    running = is_warming(record.state) and not is_parked(record.state)
    account_id = record.account_id
    return not running and owner_of(account_id) is None and not is_lent(account_id)


async def _blocked_candidates(fleet: _Fleet) -> list[BlockRecheckCandidate]:
    """Living accounts with a confirmed spam block on record that a pass may ask about.

    A block the stored answer already lifts is lifted here instead, without asking — on
    any account, busy or not: the lift touches only the database. It catches a lift the
    spam check's own hook failed to write.
    """
    candidates: list[BlockRecheckCandidate] = []
    for record in await list_warming_states():
        account_id = record.account_id
        if record.spam_blocked_at is None or account_id not in fleet.proxies:
            continue
        if await lift_from_stored_answer(account_id) or not _free_of_runtimes(record):
            continue
        last = fleet.answered.get(account_id)
        candidates.append(BlockRecheckCandidate(account_id, fleet.proxies[account_id], last))
    return candidates


async def _recheck_spam_block(fleet: _Fleet, pacing: ProbePacing) -> str | None:
    """Re-ask @SpamBot about one spam-blocked account; a clean answer lifts the block."""
    interval = timedelta(hours=settings.survival.block_recheck_hours)
    candidates = await _blocked_candidates(fleet)
    due = pick_block_recheck(candidates, fleet.asked_by_proxy, _state.attempts, pacing, interval)
    if due is None:
        return None
    _state.attempts[due.account_id] = pacing.now
    _state.asked_by_proxy[due.proxy_key] = pacing.now
    # The ask itself lifts the block on a clean answer (``services.spam_block_lift``).
    verdict = await refresh_spam_status(due.account_id)
    await log_event(
        "INFO",
        "survival_spam_block_recheck",
        account_id=due.account_id,
        extra={"status": verdict.status},
    )
    return due.account_id


async def run_probe_pass(*, now: datetime | None = None) -> str | None:
    """Ask @SpamBot about the most overdue released account; its id, or ``None``.

    With no probe due, the pass re-asks about one spam-blocked account instead.
    """
    if not settings.survival.probes_enabled:
        return None
    moment = now or datetime.now(UTC)
    everyone = (await list_accounts()).accounts
    await _record_missed_deaths(everyone)
    fleet = await _fleet(everyone)
    pacing = _pacing(moment)
    due = pick_due_probe(_candidates(fleet), fleet.asked_by_proxy, _state.attempts, pacing)
    if due is None:
        return await _recheck_spam_block(fleet, pacing)
    _state.attempts[due.account_id] = moment
    _state.asked_by_proxy[due.proxy_key] = moment
    verdict = await refresh_spam_status(due.account_id)
    await log_event(
        "INFO",
        "survival_probe",
        account_id=due.account_id,
        extra={"window": due.window, "status": verdict.status},
    )
    return due.account_id


async def _loop() -> None:
    await asyncio.sleep(settings.survival.probe_startup_delay_seconds)
    while True:
        try:
            await run_probe_pass()
        except Exception as exc:  # noqa: BLE001 - one failed pass must not end the loop.
            logger.warning("survival probe pass failed", exc_info=exc)
            await log_event(
                "WARNING",
                "survival_probe_failed",
                extra={"error_type": type(exc).__name__},
            )
        await asyncio.sleep(settings.survival.probe_interval_seconds)


async def start_survival_probes() -> None:
    """Start the hourly probe task (lifespan startup); a no-op when switched off or running."""
    if not settings.survival.probes_enabled or _state.task is not None:
        return
    _state.task = asyncio.create_task(_loop(), name="survival-probes")


async def shutdown_survival_probes() -> None:
    """Cancel the probe task and wait for it to unwind."""
    task, _state.task = _state.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def reset_for_tests() -> None:
    """Forget the task and the in-memory pacing between tests."""
    global _state  # noqa: PLW0603 - one module-level runtime, reset between tests
    _state = _State()
