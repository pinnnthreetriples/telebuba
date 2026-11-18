"""The gateway: the one way to ask "what is this account's spam status".

An account that does not exist, whose session is not alive or that has no proxy is never asked,
whatever the master switch says. Then a request is served, in order, from (1) a stored reading
inside the cache TTL (unless the caller forces, or asks for a MEASUREMENT or as the OPERATOR:
those want a real answer), (2) a probe already in flight for the account (single-flight, see
``_flight``: concurrent callers, forced or not, share ONE real probe), (3) the last real reading
or failure when it is younger than the coalescing window, and only then (4) a new probe, if the
budget (``services.spam_intel._policy``) says yes. Deciding and writing the attempt row are one
step (``_admission``). An answer the budget refused or postponed is an ``unknown`` verdict that
SAYS so (``served_from``, ``retry_at``, ``reason``), so a caller can tell it from a reading that
came back unreadable.

State is in the database (``spam_probe_attempts`` / ``spam_probe_state``), so the budget
survives a restart. Only in-flight bookkeeping and log throttling live in memory, which one
uvicorn worker makes sound.

``SPAM_INTEL__ENABLED=false`` bypasses the budget and the ledger: the call goes to the
pre-gateway ``services.spam_status.refresh_spam_status`` after the same guard.
"""

from __future__ import annotations

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
from core.phone_geo import timezone_for_phone
from core.repositories.spam_probe_ledger import finish_attempt, load_budget_snapshot
from schemas.spam_intel import ProbeAttemptFinish, Purpose
from services import spam_status
from services.spam_intel import _admission, _human
from services.spam_intel._admission import Admission, hold, reserve, unaskable
from services.spam_intel._answers import (
    ASKABLE,
    CALLER_PURPOSE,
    TRANSIENT,
    Ask,
    Ended,
    busy,
    coalesced,
    forget_logged,
    log_unasked,
    outcome_label,
    unasked,
)
from services.spam_intel._attempts import finish_quietly, prune, remember
from services.spam_intel._evidence import record_reading
from services.spam_intel._flight import INFLIGHT as _INFLIGHT
from services.spam_intel._flight import close_flight, join, open_flight
from services.spam_intel._night import night_until
from services.spam_intel._policy import Decision, ProbeRequest, parse_moment
from services.spam_intel._policy import decide as _decide
from services.spam_intel._served import evidence_hint, forget_served
from services.spam_intel._shadow import fold_before, record_comparison

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from schemas.accounts import AccountRead
    from schemas.spam_intel import BudgetSnapshot
    from schemas.spam_probe import HumanProbePlan
    from schemas.spam_status import SpamStatusVerdict

logger = logging.getLogger(__name__)

_CALLER: ContextVar[str | None] = ContextVar("spam_intel_caller", default=None)

# These asks exist to get a REAL answer (design 5.2): the survival window's product is the
# reading itself, and the operator's button is a click for a fresh one. Neither is satisfied
# from a stored reading, the fold or the cache; the budget alone decides whether they probe.
_REAL_ANSWER_ONLY = frozenset({"measurement", "operator"})
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
    """Forget in-flight bookkeeping, the gate and the log throttle; age the process (tests only)."""
    global _STARTED  # noqa: PLW0603 - one process-wide start instant, reset between tests
    _INFLIGHT.clear()
    _admission.reset()
    forget_logged()
    forget_served()
    _STARTED = datetime.now(UTC) - timedelta(days=1)


def _human_plan() -> dict[str, HumanProbePlan]:
    """The conversation's pauses as the executor's keyword, or nothing while the switch is off."""
    intel = settings.spam_intel
    return {"plan": _human.draw_plan(rng, intel)} if intel.human_sequence else {}


async def _probe(
    account: AccountRead,
    cached: SpamStatusVerdict | None,
    req: Ask,
    admitted: Admission,
    attempt_id: int,
) -> SpamStatusVerdict:
    """Ask @SpamBot for real: the row is already reserved; the executor, then the state."""
    clock = time.monotonic()
    account_id = account.account_id
    try:
        if admitted.decision.warning == "soft_cap":
            intel = settings.spam_intel
            await log_event(
                "WARNING",
                "spam_intel_budget_exceeded",
                account_id=account_id,
                extra={"cap": "soft", "limit": intel.fleet_soft_cap, "caller": req.caller},
            )
        before = await fold_before(account_id)  # shadow comparison: the fold's word first
        outcome = await spam_status.probe_and_store(account_id, previous=cached, **_human_plan())
    except BaseException as exc:
        await finish_quietly(attempt_id, type(exc).__name__, now(), clock)
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
    await remember(req, ended)
    await log_event(
        "INFO",
        "spam_intel_probe",
        account_id=account_id,
        extra={
            "caller": req.caller,
            "purpose": req.purpose,
            "outcome": ended.label,
            "duration_ms": duration_ms,
        },
    )
    if outcome.error is None:
        # The reading is evidence too (``spambot_*``): the fold and the stored projection see it.
        await record_reading(account_id, outcome.verdict.status, outcome.verdict.detail, ended.at)
        await record_comparison(account_id, before, outcome.verdict.status)
    await prune(ended.at)
    return outcome.verdict.model_copy(update={"served_from": "probe"})


async def _decide_at(
    account: AccountRead,
    req: Ask,
    at: datetime,
) -> tuple[Decision, BudgetSnapshot]:
    """The budget's word on one ask at ``at``, with the ledger snapshot it was made from."""
    intel = settings.spam_intel
    snapshot = await load_budget_snapshot(
        account.account_id, account.proxy_id, (at - timedelta(hours=24)).isoformat()
    )
    request = ProbeRequest(
        purpose=req.purpose,
        now=at,
        alive=account.status in ASKABLE | TRANSIENT,
        transient=account.status in TRANSIENT,
        night_until=night_until(at, timezone_for_phone(account.phone), account.account_id, intel),
        has_proxy=account.proxy_id is not None,
        busy=busy(account.account_id, req.holder),
        confirmed=req.confirmed,
        process_age=process_age(),
        fleet_gap=timedelta(
            seconds=rng.uniform(intel.fleet_gap_min_seconds, intel.fleet_gap_max_seconds)
        ),
    )
    return _decide(snapshot, request, intel), snapshot


def _no_live_session(at: datetime) -> Decision:
    """A routine ask for an account with no client open: later, not a connection made to ask."""
    retry_at = at + timedelta(minutes=settings.spam_intel.live_session_retry_minutes)
    return Decision(action="defer", reason="no_live_session", retry_at=retry_at)


async def _admit(account: AccountRead, req: Ask, at: datetime) -> Admission:
    """The budget's word, and when it says probe, the attempt row: ONE step under the gate.

    With ``live_session_embedding`` off this is the budget at ``at``, nothing more. On, a ROUTINE
    or DECISION ask the budget lets through waits a jittered moment when the account's client is
    already connected (the budget is then asked again: the clock moved), and a ROUTINE ask for an
    account with no live client is deferred. The wait happens with the gate RELEASED.
    """
    intel = settings.spam_intel
    async with _admission.gate():
        decision, snapshot = await _decide_at(account, req, at)
        if decision.action != "probe":
            return Admission(decision, snapshot, at)
        moment = _human.live_moment(req.account_id, req.purpose, intel)
        if moment is _human.Moment.DEFER:
            return Admission(_no_live_session(at), snapshot, at)
        if moment is _human.Moment.NOW:
            return Admission(decision, snapshot, at, await reserve(account, req, at))
    await _human.sleep(_human.live_delay(rng, intel))
    at = now()
    if req.purpose == "routine" and not _human.is_live(req.account_id):
        return Admission(_no_live_session(at), snapshot, at)  # the session ended while we waited
    async with _admission.gate():
        decision, snapshot = await _decide_at(account, req, at)
        attempt_id = await reserve(account, req, at) if decision.action == "probe" else None
        return Admission(decision, snapshot, at, attempt_id)


async def _unasked(
    req: Ask, cached: SpamStatusVerdict | None, admitted: Admission
) -> SpamStatusVerdict:
    """The answer for an ask the budget did not let probe: the window's reading, or the reason."""
    decision, at = admitted.decision, admitted.at
    await log_unasked(req.account_id, decision, req, at)
    if decision.action == "coalesce":
        return coalesced(req.account_id, cached, admitted.snapshot, at)
    if decision.reason is None:  # pragma: no cover - a probe always has its row, the rest a reason
        msg = f"the budget said {decision.action} without a reason"
        raise RuntimeError(msg)
    return unasked(
        req.account_id,
        served_from="deferred" if decision.action == "defer" else "refused",
        reason=decision.reason,
        at=at,
        retry_at=decision.retry_at,
    )


async def _ask_once(req: Ask) -> SpamStatusVerdict:
    at = now()
    account_id = req.account_id
    account = await fetch_account(account_id)
    if account is None:
        # Guard, don't probe: see ``spam_status.refresh_spam_status``.
        return await hold(req, ("account_missing", None), at, detail="account not found")
    cached = await get_spam_status(account_id)
    # Empty unless ``passive_decisions`` is on (``_served``): then the evidence may say the
    # cache cannot be trusted (``recheck``) or answer without a probe (``answer``).
    hint = await evidence_hint(req, cached, at)
    stored_may_serve = not req.force and req.purpose not in _REAL_ANSWER_ONLY
    if (
        stored_may_serve
        and not hint.recheck
        and cached is not None
        and spam_status.is_reading_fresh(cached.checked_at, at)
    ):
        return cached.model_copy(update={"served_from": "cache"})
    if hint.answer is not None and stored_may_serve:
        return hint.answer
    admitted = await _admit(account, req, at)
    if admitted.attempt_id is None:
        return await _unasked(req, cached, admitted)
    return await _probe(account, cached, req, admitted, admitted.attempt_id)


async def _legacy(req: Ask) -> SpamStatusVerdict:
    """The pre-gateway funnel, behind the guard that holds in both paths (rules 1 and 2, D1).

    ``TELEGRAM__ALLOW_DIRECT_CONNECTION`` only decides whether ``core`` would connect an account
    with no proxy; with it on, this guard is what keeps the rollback switch from asking one. An
    account the guard refuses still gets the stored reading back when it is fresh and the caller
    did not force: that asks nothing.
    """
    at = now()
    account = await fetch_account(req.account_id)
    if account is None:
        return await hold(req, ("account_missing", None), at, detail="account not found")
    held = unaskable(account, at)
    if held is not None:
        stored = await get_spam_status(req.account_id)
        if req.force or stored is None or not spam_status.is_reading_fresh(stored.checked_at, at):
            return await hold(req, held, at)
    return await spam_status.refresh_spam_status(req.account_id, force=req.force)


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
    label = caller or _CALLER.get() or "unspecified"
    wanted: Purpose = purpose or CALLER_PURPOSE.get(label) or ("decision" if force else "routine")
    req = Ask(account_id, force, wanted, label, holder, confirm_out_of_turn)
    if not settings.spam_intel.enabled:
        return await _legacy(req)
    if (shared := await join(account_id)) is not None:
        return shared
    future = open_flight(account_id)
    result: SpamStatusVerdict | None = None
    try:
        result = await _ask_once(req)
    finally:
        close_flight(account_id, future, result)
    return result


def bound_asker(holder: str | None) -> Callable[..., Awaitable[SpamStatusVerdict]]:
    """An ``(account_id, *, force=False)`` asker for one domain's seam (``holder`` fixed)."""

    async def refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:
        return await ask(account_id, force=force, holder=holder)

    return refresh


async def operator_check(account_id: str, *, confirm_out_of_turn: bool) -> SpamStatusVerdict:
    """The Spam-check button: a fresh reading if the budget allows, else what is known.

    ``confirm_out_of_turn``: the operator confirmed a click while the account's last probe is
    under ``out_of_turn_hours`` old; without it such a click comes back ``refused`` with the
    reason ``needs_confirmation`` (``services.spam_intel._card`` turns that into the route's 409).
    The 30-minute minimum, the operator's daily cap and the fleet caps bound it either way.

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
