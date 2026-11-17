"""The probe budget as a pure decision: may this account be asked about now, and if not, when.

Everything it reads comes in as arguments — the ledger snapshot, the clock, the jitter the
caller drew, the settings — so every rule is a table-driven test and nothing here touches
Telegram, the database or ``random``. The order of the rules is part of the contract
(``docs/spam-intel.md`` section 5.5): cheap, permanent refusals first, then the coalescing
window, then the caps, then the spacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from core._config_spam_intel import SpamIntelSettings
    from schemas.spam_intel import BudgetSnapshot, Purpose, Reason

_DAY = timedelta(hours=24)
_PURPOSES_WITH_BACKOFF = frozenset({"routine", "decision", "measurement"})


@dataclass(frozen=True)
class ProbeRequest:
    """One caller's ask, with the facts the policy cannot look up itself."""

    purpose: Purpose
    now: datetime
    alive: bool
    has_proxy: bool
    # Another feature holds the account and the caller is not that feature.
    busy: bool
    # The operator confirmed an out-of-turn probe (the Spam-check button).
    confirmed: bool
    # Time since this process started (routine probes wait out a restart storm).
    process_age: timedelta
    # The gap the caller drew for this ask; the spacing rule compares against it.
    fleet_gap: timedelta


@dataclass(frozen=True)
class Decision:
    """What the budget says. ``coalesce``: hand back the last reading (or failure)."""

    action: Literal["probe", "coalesce", "defer", "refuse"]
    reason: Reason | None = None
    retry_at: datetime | None = None
    # ``soft_cap``: the probe goes ahead past the soft cap and the operator should hear.
    warning: Literal["soft_cap"] | None = None


def parse_moment(value: str | None) -> datetime | None:
    """An ISO instant as an aware UTC datetime; a naive one is read as UTC."""
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def backoff_until(fail_streak: int, now: datetime, cfg: SpamIntelSettings) -> datetime:
    """When an account may be asked again after ``fail_streak`` unreadable answers in a row.

    ``backoff_base_hours`` for the first, doubling per further failure, capped at
    ``backoff_max_hours`` (6, 12, 24, 48 by default).
    """
    streak = max(fail_streak, 1)
    hours = min(cfg.backoff_base_hours * 2 ** (streak - 1), cfg.backoff_max_hours)
    return now + timedelta(hours=hours)


def _later(instant: str | None, span: timedelta) -> datetime | None:
    moment = parse_moment(instant)
    return None if moment is None else moment + span


def _defer(reason: Reason, retry_at: datetime) -> Decision:
    return Decision(action="defer", reason=reason, retry_at=retry_at)


def _operator_rule(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """The Spam-check button's own limits: a daily cap, and confirmation when out of turn."""
    if snapshot.operator_count_24h >= cfg.operator_daily_cap:
        return Decision(action="refuse", reason="operator_daily_cap")
    recent = _later(snapshot.last_attempt_started_at, timedelta(hours=cfg.out_of_turn_hours))
    if recent is not None and recent > request.now and not request.confirmed:
        return Decision(action="refuse", reason="needs_confirmation")
    return None


def _backlog_rule(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """Rules for routine, decision and measurement asks, before the spacing."""
    now = request.now
    state = snapshot.state
    next_allowed = parse_moment(state.next_allowed_at) if state is not None else None
    if (
        request.purpose in _PURPOSES_WITH_BACKOFF
        and next_allowed is not None
        and next_allowed > now
    ):
        return _defer("backoff", next_allowed)
    if snapshot.account_count_24h >= cfg.account_daily_cap:
        return _defer("account_daily_cap", _later(snapshot.account_oldest_24h, _DAY) or now + _DAY)
    if request.purpose == "routine" and request.process_age < timedelta(
        minutes=cfg.startup_quiet_minutes
    ):
        return _defer(
            "startup_quiet",
            now + timedelta(minutes=cfg.startup_quiet_minutes) - request.process_age,
        )
    if request.busy:
        return _defer("busy", now + timedelta(minutes=cfg.busy_retry_minutes))
    return None


def _spacing_rule(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """No two real probes behind one proxy close together, nor two anywhere in the fleet."""
    proxy_free = _later(
        snapshot.last_proxy_started_at, timedelta(minutes=cfg.proxy_spacing_minutes)
    )
    if proxy_free is not None and proxy_free > request.now:
        return _defer("proxy_spacing", proxy_free)
    fleet_free = _later(snapshot.last_fleet_started_at, request.fleet_gap)
    if fleet_free is not None and fleet_free > request.now:
        return _defer("fleet_spacing", fleet_free)
    return None


def _gate(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """The rules every purpose meets first: who may be asked, the window, the hard cap."""
    now = request.now
    if not request.alive:
        return Decision(action="refuse", reason="account_not_alive")
    if not request.has_proxy:
        return Decision(action="refuse", reason="no_proxy")
    last = _later(snapshot.last_attempt_started_at, timedelta(minutes=cfg.coalesce_minutes))
    if last is not None and last > now:
        return Decision(action="coalesce")
    if snapshot.fleet_count_24h >= cfg.fleet_hard_cap:
        return _defer("fleet_hard_cap", _later(snapshot.fleet_oldest_24h, _DAY) or now + _DAY)
    return None


def _soft_cap(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision:
    """Past the soft cap routine and measurement wait; a decision goes on, with a warning."""
    if snapshot.fleet_count_24h < cfg.fleet_soft_cap:
        return Decision(action="probe")
    if request.purpose in {"routine", "measurement"}:
        retry = _later(snapshot.fleet_oldest_24h, _DAY) or request.now + _DAY
        return _defer("fleet_soft_cap", retry)
    return Decision(action="probe", warning="soft_cap")


def decide(snapshot: BudgetSnapshot, request: ProbeRequest, cfg: SpamIntelSettings) -> Decision:
    """The budget's answer for one ask; see the module docstring for the rule order."""
    gated = _gate(snapshot, request, cfg)
    if gated is not None:
        return gated
    if request.purpose == "operator":
        # A human click: no backoff, spacing or busy rule, only its own limits.
        return _operator_rule(snapshot, request, cfg) or _soft_cap(snapshot, request, cfg)
    for rule in (_backlog_rule, _spacing_rule):
        deferred = rule(snapshot, request, cfg)
        if deferred is not None:
            return deferred
    return _soft_cap(snapshot, request, cfg)


def decide_history(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision:
    """May the dialog with @SpamBot be READ now (the history sweep, nothing is sent).

    The same permanent refusals and the same spacing as a probe — it is a conversation with
    the bot too — but none of the probe caps, windows or backoff: a read neither spends the
    account's probe budget nor repeats one.
    """
    if not request.alive:
        return Decision(action="refuse", reason="account_not_alive")
    if not request.has_proxy:
        return Decision(action="refuse", reason="no_proxy")
    if request.busy:
        return _defer("busy", request.now + timedelta(minutes=cfg.busy_retry_minutes))
    return _spacing_rule(snapshot, request, cfg) or Decision(action="probe")
