"""The gateway: the one way to ask "what is this account's spam status".

A request is served, in order, from (1) a stored reading inside the cache TTL (unless the
caller forces), (2) a probe already in flight for the account (single-flight: concurrent
callers, forced or not, share ONE probe), (3) the last real reading or failure when it is
younger than the coalescing window, and only then (4) a new probe, if the budget
(``services.spam_intel._policy``) says yes. An answer the budget refused or postponed is an
``unknown`` verdict that SAYS so (``served_from``, ``retry_at``, ``reason``), so a caller can
tell it from a reading that came back unreadable.

State is in the database (``spam_probe_attempts`` / ``spam_probe_state``), so the budget
survives a restart. Only in-flight bookkeeping and log throttling live in memory, which one
uvicorn worker makes sound.

``SPAM_INTEL__ENABLED=false`` bypasses all of it: the call goes straight to the pre-gateway
``services.spam_status.refresh_spam_status``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account, get_spam_status
from core.logging import log_event
from core.repositories.spam_probe_ledger import (
    finish_attempt,
    load_budget_snapshot,
    prune_attempts,
    save_probe_state,
    start_attempt,
)
from schemas.spam_intel import (
    ProbeAttemptFinish,
    ProbeAttemptStart,
    ProbeState,
    Purpose,
)
from services import spam_status
from services.spam_intel._answers import (
    CALLER_PURPOSE,
    UNASKABLE,
    Ask,
    Ended,
    busy,
    coalesced,
    forget_logged,
    log_unasked,
    outcome_label,
    unasked,
)
from services.spam_intel._policy import ProbeRequest, backoff_until, parse_moment
from services.spam_intel._policy import decide as _decide

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from schemas.accounts import AccountRead
    from schemas.spam_status import SpamStatusVerdict

logger = logging.getLogger(__name__)

_CALLER: ContextVar[str | None] = ContextVar("spam_intel_caller", default=None)

# One probe per account at a time. The value is the leader's verdict, or ``None`` when the
# leader failed or was cancelled and its waiters must ask for themselves.
_INFLIGHT: dict[str, asyncio.Future[SpamStatusVerdict | None]] = {}
# SystemRandom: non-cryptographic jitter; avoids ruff S311. Patched by tests.
rng = random.SystemRandom()
_STARTED = datetime.now(UTC)


def now() -> datetime:
    """The gateway's one clock — patched by tests."""
    return datetime.now(UTC)


def process_age() -> timedelta:
    """How long this process has been up (routine probes wait out a restart storm)."""
    return now() - _STARTED


@contextmanager
def caller_scope(label: str) -> Iterator[None]:
    """Name who is asking for everything awaited inside the block (events, ledger rows)."""
    token = _CALLER.set(label)
    try:
        yield
    finally:
        _CALLER.reset(token)


def reset_for_tests() -> None:
    """Forget in-flight bookkeeping and the log throttle; age the process (tests only)."""
    global _STARTED  # noqa: PLW0603 - one process-wide start instant, reset between tests
    _INFLIGHT.clear()
    forget_logged()
    _STARTED = datetime.now(UTC) - timedelta(days=1)


async def _remember(req: Ask, ended: Ended, previous: ProbeState | None) -> None:
    """Fold one finished attempt into the account's state: streak, backoff, last failure."""
    readable = ended.label in {"clean", "limited"}
    streak = 0 if readable else (previous.fail_streak if previous else 0) + 1
    stamp = ended.at.isoformat()
    last_success = stamp if readable else (previous.last_success_at if previous else None)
    next_allowed = (
        None if readable else backoff_until(streak, ended.at, settings.spam_intel).isoformat()
    )
    await save_probe_state(
        ProbeState(
            account_id=req.account_id,
            last_attempt_at=stamp,
            last_success_at=last_success,
            fail_streak=streak,
            next_allowed_at=next_allowed,
            last_caller=req.caller,
            last_outcome=ended.label,
            last_error=ended.error,
            history_read_at=previous.history_read_at if previous else None,
            updated_at=stamp,
        )
    )


async def _finish_quietly(attempt_id: int, error: str, clock: float) -> None:
    try:
        await finish_attempt(
            ProbeAttemptFinish(
                attempt_id=attempt_id,
                finished_at=now().isoformat(),
                outcome="error",
                error_type=error,
                duration_ms=int((time.monotonic() - clock) * 1000),
            )
        )
    except Exception as exc:  # noqa: BLE001 - closing a ledger row must not mask the real error.
        logger.warning("spam attempt finish failed: %s", type(exc).__name__)


async def _prune() -> None:
    intel = settings.spam_intel
    cutoff = (now() - timedelta(days=intel.attempts_retention_days)).isoformat()
    try:
        await prune_attempts(cutoff, intel.attempts_max_rows)
    except Exception as exc:  # noqa: BLE001 - housekeeping must not fail a probe.
        logger.warning("spam attempt prune failed: %s", type(exc).__name__)


async def _probe(
    account: AccountRead,
    cached: SpamStatusVerdict | None,
    previous: ProbeState | None,
    req: Ask,
) -> SpamStatusVerdict:
    """Ask @SpamBot for real: ledger row first, then the executor, then the state."""
    attempt_id = await start_attempt(
        ProbeAttemptStart(
            account_id=account.account_id,
            proxy_key=account.proxy_id,
            purpose=req.purpose,
            caller=req.caller,
            started_at=now().isoformat(),
        )
    )
    clock = time.monotonic()
    try:
        outcome = await spam_status.probe_and_store(account.account_id, previous=cached)
    except BaseException as exc:
        await _finish_quietly(attempt_id, type(exc).__name__, clock)
        raise
    ended = Ended(outcome_label(outcome.verdict, outcome.error), outcome.error, now())
    duration_ms = int((time.monotonic() - clock) * 1000)
    await finish_attempt(
        ProbeAttemptFinish(
            attempt_id=attempt_id,
            finished_at=ended.at.isoformat(),
            outcome=ended.label,
            error_type=ended.error,
            duration_ms=duration_ms,
        )
    )
    await _remember(req, ended, previous)
    await log_event(
        "INFO",
        "spam_intel_probe",
        account_id=account.account_id,
        extra={
            "caller": req.caller,
            "purpose": req.purpose,
            "outcome": ended.label,
            "duration_ms": duration_ms,
        },
    )
    await _prune()
    return outcome.verdict.model_copy(update={"served_from": "probe"})


async def _ask_once(req: Ask) -> SpamStatusVerdict:
    at = now()
    account_id = req.account_id
    account = await fetch_account(account_id)
    if account is None:
        # Guard, don't probe: see ``spam_status.refresh_spam_status``.
        return unasked(
            account_id,
            served_from="refused",
            reason="account_missing",
            at=at,
            detail="account not found",
        )
    cached = await get_spam_status(account_id)
    if not req.force and cached is not None and spam_status.is_reading_fresh(cached.checked_at, at):
        return cached.model_copy(update={"served_from": "cache"})
    intel = settings.spam_intel
    snapshot = await load_budget_snapshot(
        account_id, account.proxy_id, (at - timedelta(hours=24)).isoformat()
    )
    request = ProbeRequest(
        purpose=req.purpose,
        now=at,
        alive=account.status not in UNASKABLE,
        has_proxy=account.proxy_id is not None,
        busy=busy(account_id, req.holder),
        confirmed=req.confirmed,
        process_age=process_age(),
        fleet_gap=timedelta(
            seconds=rng.uniform(intel.fleet_gap_min_seconds, intel.fleet_gap_max_seconds)
        ),
    )
    decision = _decide(snapshot, request, intel)
    if decision.action == "coalesce":
        await log_unasked(account_id, decision, req, at)
        return coalesced(account_id, cached, snapshot.state, at)
    if decision.reason is not None:
        await log_unasked(account_id, decision, req, at)
        return unasked(
            account_id,
            served_from="deferred" if decision.action == "defer" else "refused",
            reason=decision.reason,
            at=at,
            retry_at=decision.retry_at,
        )
    if decision.warning == "soft_cap":
        await log_event(
            "WARNING",
            "spam_intel_budget_exceeded",
            account_id=account_id,
            extra={"cap": "soft", "limit": intel.fleet_soft_cap, "caller": req.caller},
        )
    return await _probe(account, cached, snapshot.state, req)


async def ask(  # noqa: PLR0913 - the public asker; every knob is a keyword.
    account_id: str,
    *,
    force: bool = False,
    purpose: Purpose | None = None,
    caller: str | None = None,
    holder: str | None = None,
    confirm_out_of_turn: bool = False,
) -> SpamStatusVerdict:
    """The account's spam-status verdict, probing @SpamBot only when the budget allows.

    ``force`` skips the cache TTL (a reading younger than the coalescing window is still
    handed back). ``holder`` is the feature the caller IS (``warming``, ``chat_broadcast``):
    an account another feature holds is not probed in parallel, but the caller's own is.
    ``caller`` defaults to the label set by :func:`caller_scope`.
    """
    if not settings.spam_intel.enabled:
        return await spam_status.refresh_spam_status(account_id, force=force)
    label = caller or _CALLER.get() or "unspecified"
    wanted: Purpose = purpose or CALLER_PURPOSE.get(label) or ("decision" if force else "routine")
    while (leader := _INFLIGHT.get(account_id)) is not None:
        shared = await asyncio.shield(leader)
        if shared is not None:
            joined = "coalesced" if shared.served_from == "probe" else shared.served_from
            return shared.model_copy(update={"served_from": joined})
    future: asyncio.Future[SpamStatusVerdict | None] = asyncio.get_running_loop().create_future()
    _INFLIGHT[account_id] = future
    result: SpamStatusVerdict | None = None
    try:
        result = await _ask_once(Ask(account_id, force, wanted, label, holder, confirm_out_of_turn))
    finally:
        _INFLIGHT.pop(account_id, None)
        future.set_result(result)
    return result


def bound_asker(holder: str | None) -> Callable[..., Awaitable[SpamStatusVerdict]]:
    """An ``(account_id, *, force=False)`` asker for one domain's seam (``holder`` fixed)."""

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        return await ask(account_id, force=force, holder=holder)

    return refresh


async def operator_check(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:
    """The Spam-check button: a fresh reading if the budget allows, else what is known.

    The button has no confirmation dialog yet, so its route passes ``confirm_out_of_turn=True``;
    the 30-minute minimum, the operator's daily cap and the fleet caps still bound it.

    When the budget refuses or postpones, the stored verdict comes back with the reason, so
    the operator keeps seeing the status instead of an ``unknown``.
    """
    verdict = await ask(
        account_id,
        force=True,
        purpose="operator",
        caller="api.operator",
        confirm_out_of_turn=confirm_out_of_turn,
    )
    if verdict.served_from not in {"deferred", "refused"}:
        return verdict
    stored = await get_spam_status(account_id)
    if stored is None:
        return verdict
    return stored.model_copy(
        update={
            "served_from": verdict.served_from,
            "retry_at": verdict.retry_at,
            "reason": verdict.reason,
        }
    )


def parse_retry(verdict: SpamStatusVerdict) -> datetime | None:
    """``retry_at`` of an unasked answer as an aware datetime."""
    return parse_moment(verdict.retry_at)
