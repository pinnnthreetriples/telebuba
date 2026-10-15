"""Survival probes: ask @SpamBot on day 7 and day 30 after an account's release (3.3).

One background task, started in the lifespan, makes one pass an hour. A pass first
journals any death no session check recorded (an action found the account frozen), so
the counter sees it, then asks about at most one account (``services.survival._due``).
The ask is the same cached ``refresh_spam_status`` the warming cycle and the hand-off
use — no second Telegram path: a fresh cached answer is reused, a new one lands in the
journal as ``spambot_verdict``.
Attempts and the per-proxy spacing are remembered in memory, which is safe because one
uvicorn worker runs; after a restart the journal's own answers still space the asks.
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
from services.lifecycle_journal import DEAD_SESSION_STATUSES, record_session_death
from services.spam_status import refresh_spam_status
from services.survival._due import DIRECT, ProbeCandidate, ProbePacing, pick_due_probe
from services.survival._judging import check_grace
from services.survival._outcomes import life_facts
from services.survival._stints import build_lives, parse_moment

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

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


async def _candidates(
    everyone: list[AccountRead],
) -> tuple[list[ProbeCandidate], dict[str, datetime]]:
    """Living accounts' current lives, and the latest journal answer behind each proxy."""
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
    asked = dict(_state.asked_by_proxy)
    for event in events:
        moment = parse_moment(event.created_at)
        if event.kind != "spambot_verdict" or moment is None:
            continue
        key = proxies[event.account_id]
        asked[key] = max(asked.get(key, moment), moment)
    candidates = [
        ProbeCandidate(life.account_id, proxies[life.account_id], life_facts(life))
        for life in build_lives(events)
        if life.current
    ]
    return candidates, asked


async def run_probe_pass(*, now: datetime | None = None) -> str | None:
    """Ask @SpamBot about the most overdue released account; its id, or ``None``."""
    if not settings.survival.probes_enabled:
        return None
    moment = now or datetime.now(UTC)
    everyone = (await list_accounts()).accounts
    await _record_missed_deaths(everyone)
    candidates, asked = await _candidates(everyone)
    due = pick_due_probe(candidates, asked, _state.attempts, _pacing(moment))
    if due is None:
        return None
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
