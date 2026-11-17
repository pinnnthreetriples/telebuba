"""The read-only history sweep: collect what @SpamBot already said, without asking again.

The probe never deleted its dialog, so every account's past replies are still in it. One
``messages.getHistory`` per account (no ``/start``, nothing marked read) yields real reply
texts in the fleet's languages — the fixtures the multi-language parser needs, and a way to
see whether the accounts whose language the parser cannot read are in fact limited. The texts
go to ``spam_reply_samples`` redacted; the stored verdict is NOT touched.

It is a Telegram request on every eligible account, so it is off (``SPAM_INTEL__HISTORY_SWEEP``)
unless the owner switches it on. It runs once per account (``history_read_at``), through the
same eligibility and spacing as a probe: alive, with a proxy, not busy, one account behind a
proxy at a time, a jittered gap between any two.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.accounts import list_accounts
from core.repositories.spam_probe_ledger import (
    fetch_probe_state,
    finish_attempt,
    load_budget_snapshot,
    save_probe_state,
    start_attempt,
)
from core.telegram_client import read_spambot_history
from schemas.spam_intel import ProbeAttemptFinish, ProbeAttemptStart, ProbeState
from services.spam_intel import _gateway
from services.spam_intel._answers import UNASKABLE, busy
from services.spam_intel._policy import ProbeRequest, decide_history
from services.spam_status import store_reply_sample

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

logger = logging.getLogger(__name__)

# A wait the budget asks for beyond this is not worth holding the sweep for: the account is
# skipped and read on the next run.
_MAX_WAIT = timedelta(minutes=30)
_MAX_TRIES = 6


class _Task:
    task: asyncio.Task[None] | None = None


_task = _Task()


async def _sleep(seconds: float) -> None:
    """Sleep seam — patched to a no-op in tests."""
    await asyncio.sleep(seconds)


async def _wait_for_budget(account: AccountRead) -> bool:
    """Wait until the account may be read, or say it may not be (this run)."""
    intel = settings.spam_intel
    for _ in range(_MAX_TRIES):
        at = _gateway.now()
        snapshot = await load_budget_snapshot(
            account.account_id, account.proxy_id, (at - timedelta(hours=24)).isoformat()
        )
        request = ProbeRequest(
            purpose="routine",
            now=at,
            alive=account.status not in UNASKABLE,
            has_proxy=account.proxy_id is not None,
            busy=busy(account.account_id, None),
            confirmed=False,
            process_age=_gateway.process_age(),
            fleet_gap=timedelta(
                seconds=_gateway.rng.uniform(
                    intel.fleet_gap_min_seconds, intel.fleet_gap_max_seconds
                )
            ),
        )
        decision = decide_history(snapshot, request, intel)
        if decision.action == "probe":
            return True
        if decision.retry_at is None or decision.retry_at - at > _MAX_WAIT:
            return False
        await _sleep((decision.retry_at - at).total_seconds())
    return False


async def _read_one(account: AccountRead) -> int | None:
    """Read one account's dialog and keep the replies; the count, or ``None`` on a failure."""
    intel = settings.spam_intel
    attempt_id = await start_attempt(
        ProbeAttemptStart(
            account_id=account.account_id,
            proxy_key=account.proxy_id,
            kind="history",
            purpose="routine",
            caller="spam_intel.history_sweep",
            started_at=_gateway.now().isoformat(),
        )
    )
    history = await read_spambot_history(account.account_id, intel.history_messages)
    finished = _gateway.now()
    await finish_attempt(
        ProbeAttemptFinish(
            attempt_id=attempt_id,
            finished_at=finished.isoformat(),
            outcome="error" if history.error else "history",
            error_type=history.error,
        )
    )
    if history.error is not None:
        return None
    for text in history.texts:
        await store_reply_sample(account.account_id, text, "history", finished)
    previous = await fetch_probe_state(account.account_id)
    state = previous or ProbeState(account_id=account.account_id)
    await save_probe_state(
        state.model_copy(
            update={"history_read_at": finished.isoformat(), "updated_at": finished.isoformat()}
        )
    )
    return len(history.texts)


async def run_history_sweep() -> int:
    """Read the @SpamBot dialog of every eligible account once; the number of accounts read."""
    intel = settings.spam_intel
    read = 0
    for account in (await list_accounts()).accounts:
        state = await fetch_probe_state(account.account_id)
        if state is not None and state.history_read_at is not None:
            continue
        if not await _wait_for_budget(account):
            continue
        count = await _read_one(account)
        await log_event(
            "INFO" if count is not None else "WARNING",
            "spam_intel_history_read",
            account_id=account.account_id,
            extra={"messages": count, "ok": count is not None},
        )
        read += count is not None
        await _sleep(_gateway.rng.uniform(intel.fleet_gap_min_seconds, intel.fleet_gap_max_seconds))
    await log_event("INFO", "spam_intel_history_sweep_done", extra={"accounts_read": read})
    return read


async def _run() -> None:
    await _sleep(settings.spam_intel.history_startup_delay_minutes * 60)
    try:
        await run_history_sweep()
    except Exception as exc:  # noqa: BLE001 - a failed sweep must not end the app.
        logger.warning("spam history sweep failed", exc_info=exc)
        await log_event(
            "WARNING", "spam_intel_history_sweep_failed", extra={"error_type": type(exc).__name__}
        )


async def start_history_sweep() -> None:
    """Start the one-off sweep (lifespan startup); a no-op unless switched on."""
    intel = settings.spam_intel
    if not (intel.enabled and intel.history_sweep) or _task.task is not None:
        return
    _task.task = asyncio.create_task(_run(), name="spam-history-sweep")


async def shutdown_history_sweep() -> None:
    """Cancel the sweep and wait for it to unwind."""
    task, _task.task = _task.task, None
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
