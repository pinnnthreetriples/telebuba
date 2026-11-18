"""The probe budget as a pure decision: may this account be asked about now, and if not, when.

Everything it reads comes in as arguments — the ledger snapshot, the clock, the jitter the
caller drew, the settings — so every rule is a table-driven test and nothing here touches
Telegram, the database or ``random``. The order of the rules is part of the contract
(``docs/spam-intel.md`` section 5.5): who may be asked at all, then the coalescing window, then
the account's own cap, backoff, the proxy and fleet spacing, the fleet caps, and last the busy
account, the quiet start and the local night. The order decides which reason an ask is turned
away with, never whether it goes.
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
_MAX_DOUBLINGS = 32


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
    # The account's status is a transient fault (flood wait, proxy or network error): not dead
    # (``alive`` stays true), but not to be asked now.
    transient: bool = False
    # When the account's local night ends, if the ask is made inside it (``_night``).
    night_until: datetime | None = None


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
    # Clamped: past a few dozen doublings the cap is long reached, and 2 ** 1100 is not a float.
    streak = min(max(fail_streak, 1), _MAX_DOUBLINGS)
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


def _account_cap(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """The account's own daily cap (rule 4: it replaces the draft's 6 h minimum)."""
    if snapshot.account_count_24h >= cfg.account_daily_cap:
        retry = _later(snapshot.account_oldest_24h, _DAY) or request.now + _DAY
        return _defer("account_daily_cap", retry)
    return None


def _backoff_rule(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,  # noqa: ARG001 - the shape every rule shares
) -> Decision | None:
    """Rule 6: after an unreadable answer or a failure the account waits (6, 12, 24, 48 h)."""
    state = snapshot.state
    next_allowed = parse_moment(state.next_allowed_at) if state is not None else None
    if (
        request.purpose in _PURPOSES_WITH_BACKOFF
        and next_allowed is not None
        and next_allowed > request.now
    ):
        return _defer("backoff", next_allowed)
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


def _hard_cap(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """Past the hard cap nothing goes, the operator included."""
    if snapshot.fleet_count_24h >= cfg.fleet_hard_cap:
        retry = _later(snapshot.fleet_oldest_24h, _DAY) or request.now + _DAY
        return _defer("fleet_hard_cap", retry)
    return None


def _soft_cap(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """Past the soft cap routine and measurement wait (a decision goes on, with a warning)."""
    if snapshot.fleet_count_24h < cfg.fleet_soft_cap:
        return None
    if request.purpose in {"routine", "measurement"}:
        retry = _later(snapshot.fleet_oldest_24h, _DAY) or request.now + _DAY
        return _defer("fleet_soft_cap", retry)
    return None


def _occupancy_rule(
    snapshot: BudgetSnapshot,  # noqa: ARG001 - the shape every rule shares
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """Rules 11 and 12: another feature holds the account, then a routine ask in the quiet start."""
    if request.busy:
        return _defer("busy", request.now + timedelta(minutes=cfg.busy_retry_minutes))
    if request.purpose == "routine" and request.process_age < timedelta(
        minutes=cfg.startup_quiet_minutes
    ):
        return _defer(
            "startup_quiet",
            request.now + timedelta(minutes=cfg.startup_quiet_minutes) - request.process_age,
        )
    return None


def _transient_status(request: ProbeRequest, cfg: SpamIntelSettings) -> Decision | None:
    """A transient account status defers every purpose, the operator's click included.

    The session check clears it by itself, so this is a wait, not a refusal. The operator is held
    too, conservatively: a human can look at the account's status on the card, and a check sent
    through a flood wait or a failing proxy would mostly come back unreadable and cost a streak.
    """
    if not request.transient:
        return None
    retry = request.now + timedelta(minutes=cfg.account_status_retry_minutes)
    return _defer("account_status_transient", retry)


def _night_rule(
    snapshot: BudgetSnapshot,  # noqa: ARG001 - the shape every rule shares
    request: ProbeRequest,
    cfg: SpamIntelSettings,  # noqa: ARG001 - the shape every rule shares
) -> Decision | None:
    """Rule 13 (D13): a ROUTINE ask waits out the account's local night; no other purpose does."""
    until = request.night_until
    if request.purpose == "routine" and until is not None and until > request.now:
        return _defer("local_night", until)
    return None


def _gate(
    snapshot: BudgetSnapshot,
    request: ProbeRequest,
    cfg: SpamIntelSettings,
) -> Decision | None:
    """Rules 1-3, for every purpose: who may be asked at all, and the coalescing window."""
    if not request.alive:
        return Decision(action="refuse", reason="account_not_alive")
    if not request.has_proxy:
        return Decision(action="refuse", reason="no_proxy")
    last = _later(snapshot.last_attempt_started_at, timedelta(minutes=cfg.coalesce_minutes))
    if last is not None and last > request.now:
        return Decision(action="coalesce")
    return _transient_status(request, cfg)


def _go(snapshot: BudgetSnapshot, cfg: SpamIntelSettings) -> Decision:
    """The ask may probe; past the soft cap (a decision or the operator) the owner hears."""
    over = snapshot.fleet_count_24h >= cfg.fleet_soft_cap
    return Decision(action="probe", warning="soft_cap" if over else None)


# Rules 4-13 for a routine, decision or measurement ask, in the order of design 5.5.
_RULES = (
    _account_cap,
    _backoff_rule,
    _spacing_rule,
    _hard_cap,
    _soft_cap,
    _occupancy_rule,
    _night_rule,
)


def decide(snapshot: BudgetSnapshot, request: ProbeRequest, cfg: SpamIntelSettings) -> Decision:
    """The budget's answer for one ask; see the module docstring for the rule order."""
    gated = _gate(snapshot, request, cfg)
    if gated is not None:
        return gated
    if request.purpose == "operator":
        # A human click: no backoff, spacing or busy rule, only the hard cap and its own limits.
        return (
            _hard_cap(snapshot, request, cfg)
            or _operator_rule(snapshot, request, cfg)
            or _go(snapshot, cfg)
        )
    for rule in _RULES:
        refused = rule(snapshot, request, cfg)
        if refused is not None:
            return refused
    return _go(snapshot, cfg)


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
    if (transient := _transient_status(request, cfg)) is not None:
        return transient
    if request.busy:
        return _defer("busy", request.now + timedelta(minutes=cfg.busy_retry_minutes))
    return _spacing_rule(snapshot, request, cfg) or Decision(action="probe")
