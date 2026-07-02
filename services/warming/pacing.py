"""Pure pacing helpers for the warming engine — no Telegram/Gemini I/O.

Scheduling math (persona cadence, daily budget, next-run timing), human-like
delays, the phase/trust safety ceiling, FloodWait classification, and
account-local-time helpers. Kept dependency-light so the engine and the board
can both import them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import settings
from core.db import fetch_account
from core.phone_geo import timezone_for_phone
from schemas.warming import (
    ActivityPersona,
    WarmingIntensity,
    WarmingPhase,
    WarmingReadiness,
    is_warming,
)

if TYPE_CHECKING:
    from random import Random

    from schemas.accounts import AccountRead
    from schemas.spam_status import SpamStatusVerdict
    from schemas.telegram_actions import ActionResult
    from schemas.trust import TrustScore
    from schemas.warming import WarmingState, WarmingStateRecord

_SECONDS_PER_HOUR = 3600

# Age assumed for an account with a missing/unparseable ``created_at`` — old
# enough to skip the young-account throttle rather than freeze it at day-one
# behaviour (replaces the retired age-ramp's ``ramp_full_age_hours``).
_UNKNOWN_AGE_FALLBACK_HOURS = 192.0

# Rate-limit families that carry a duration and mean "wait then retry".
_WAIT_STATUSES: Final = frozenset({"flood_wait", "slow_mode_wait", "premium_wait"})
# Any status that should halt the current channel/cycle pass. ``peer_flood`` is
# a moderation restriction (no duration) handled by quarantine, not a wait.
_HALT_STATUSES: Final = _WAIT_STATUSES | {"peer_flood"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _seconds_until(next_run_at_iso: str, now: datetime) -> float:
    """Seconds from ``now`` until an ISO timestamp, never negative.

    Corrupt/naive timestamps degrade to ``0.0`` so the loop runs now rather than
    crashing or sleeping forever.
    """
    try:
        target = datetime.fromisoformat(next_run_at_iso)
    except ValueError:
        return 0.0
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - now).total_seconds())


def _in_quiet_hours(now: datetime, start_hour: int, end_hour: int) -> bool:
    """True when the hour of ``now`` falls in the ``[start, end)`` window.

    Callers pass account-local time (see ``_local_now``), so the operator's
    start/end hours are interpreted in the account's own timezone, not UTC.
    ``start == end`` means "no window" (always False). The window wraps midnight
    when ``start > end`` (e.g. 23→7).
    """
    if start_hour == end_hour:
        return False
    hour = now.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _roll_daily(record: WarmingStateRecord | None, today: str) -> tuple[int, str]:
    """Return ``(count, date)`` for today, resetting the counter on a new day."""
    if record is None or record.daily_count_date != today:
        return 0, today
    return record.daily_actions, today


def _proxy_snapshot(account: AccountRead) -> str | None:
    """Freeze which proxy an account started warming with, for later diagnosis."""
    if not account.proxy_host:
        return None
    base = f"{account.proxy_type or 'proxy'}://{account.proxy_host}:{account.proxy_port}"
    if account.proxy_country_code:
        base = f"{base} ({account.proxy_country_code})"
    return base


def evaluate_readiness(
    account: AccountRead,
    channel_count: int,
    spam: SpamStatusVerdict | None = None,
    trust_score: TrustScore | None = None,
) -> WarmingReadiness:
    """Decide whether an account can safely start warming, from last-known state.

    Uses the persisted account/proxy snapshot (no live network) so the board can
    show a badge cheaply and ``start_warming`` can refuse broken accounts.
    """
    reasons: list[str] = []
    if account.status != "alive":
        reasons.append(f"session {account.status}")
    if not account.proxy_host:
        reasons.append("no proxy")
    elif account.proxy_status == "failed":
        reasons.append("proxy failed")
    if channel_count <= 0:
        reasons.append("no channels")
    if spam and spam.status == "limited":
        reasons.append("spam limited")
    if trust_score and trust_score.band == "critical":
        reasons.append("trust critical")
    return WarmingReadiness(ready=not reasons, reasons=reasons)


def _classify_flood(result: ActionResult) -> tuple[bool, int | None, str | None]:
    """Extract (flooded, seconds, until_iso) from a wait-family ActionResult."""
    if result.status not in _WAIT_STATUSES:
        return False, None, None
    seconds = result.flood_wait_seconds
    until = None
    if seconds is not None:
        until = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    return True, seconds, until


def warming_days_since(
    started_at: str | None,
    now: datetime,
    *,
    stopped_at: str | None = None,
    state: WarmingState | None = None,
) -> int | None:
    """Whole days since ``started_at`` (ISO-8601); ``None`` when never started.

    Shared by the board card (the "в прогреве N дн" hint) and the loop's
    target-reached gate (auto-complete once N ≥ the operator's chosen duration).

    When ``state`` is supplied and is *not* a warming state (the account was
    stopped/promoted), the interval is capped at ``stopped_at`` so the count is
    frozen at the stop point rather than growing with wall-clock — otherwise a
    warmed card's "X/Y days" X would climb past Y forever and erode the
    ``min_days`` floor.
    """
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    upper = now
    if state is not None and not is_warming(state) and stopped_at:
        try:
            stopped = datetime.fromisoformat(stopped_at)
        except ValueError:
            stopped = now
        if stopped.tzinfo is None:
            stopped = stopped.replace(tzinfo=UTC)
        upper = min(now, stopped)
    return max(0, int((upper - started).total_seconds() / _SECONDS_PER_HOUR // 24))


def _account_age_hours(account: AccountRead | None, now: datetime) -> float:
    """Hours since the account was created; full-ramp age when unknown.

    A missing/unparseable ``created_at`` degrades to full intensity so an
    anomalous record never silently freezes an account at day-one behaviour.
    """
    if account is None:
        return _UNKNOWN_AGE_FALLBACK_HOURS
    try:
        created = datetime.fromisoformat(account.created_at)
    except ValueError:
        return _UNKNOWN_AGE_FALLBACK_HOURS
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0.0, (now - created).total_seconds() / _SECONDS_PER_HOUR)


# Five-phase lifecycle table — drives the per-account daily action cap and
# the visible "what stage is this account in" affordance on the card.
#
# Day bounds and caps are conservative, anchored on the lower end of the
# 2026 warming guidance spread (TelePilot Pro 14-day schedule, SMM Plus,
# CRMChat 80-100/day ceiling for accounts ≥2-3 months). The shape is
# high-confidence; the absolute numbers carry ±30% uncertainty in the
# sources, so they live here as constants for future tuning.
_PHASE_ORDER: tuple[WarmingPhase, ...] = (
    "intro",
    "settling",
    "warming",
    "active",
    "warmed",
)

# Upper day bound of each phase (inclusive). The next phase starts at
# ``bound + 1`` days. ``None`` = terminal phase (no next bound).
_PHASE_DAY_BOUND: dict[WarmingPhase, int | None] = {
    "intro": 1,
    "settling": 7,
    "warming": 14,
    "active": 29,
    "warmed": None,
}

# Daily action cap by phase. Lowered from the initial proposal after research
# found the 2026 source consensus runs ~30% under our first guesses. 80 is the
# CRMChat documented ceiling for accounts ≥2-3 months.
_PHASE_DAILY_CAP: dict[WarmingPhase, int] = {
    "intro": 3,
    "settling": 10,
    "warming": 20,
    "active": 40,
    "warmed": 80,
}

# Trust-score gate: the maximum phase a given trust band is allowed to occupy.
# A 60-day-old account with ``critical`` trust still gets the ``settling`` cap.
# ``excellent`` / ``good`` = no extra cap, age-phase rules apply.
_TRUST_PHASE_CEILING: dict[str, WarmingPhase] = {
    "excellent": "warmed",
    "good": "warmed",
    "watch": "active",
    "at_risk": "warming",
    "critical": "settling",
}

# Hard age floor: fresh accounts (< 24 hours) cannot exceed ``intro`` even with
# a clean trust score and clean proxy — the first day is the highest-risk window
# regardless of other signals. Shortened 72h→24h per the 2026-07-01 persona ADR
# (the operator's explicit speed/risk trade); ``dm_min_age_hours`` (36h) is the
# separate, deliberately-unchanged guard on the highest-risk action.
_PHASE_HARD_FLOOR_AGE_HOURS = 24.0

# Trust bands permitted to send DMs (audit П11). DM is the highest-risk action,
# so ``at_risk``/``critical`` accounts are blocked even once old enough.
_DM_ALLOWED_BANDS: Final = frozenset({"excellent", "good", "watch"})

# Activity-persona presets — the operator's chosen *target* cadence (2026-07-01
# ADR). Sessions/day (a range, drawn per next-run) plus per-session reaction and
# inter-account DM probability. Lives here beside the phase table (its safety-
# ceiling counterpart), same "documented tuning constants" rationale — effective
# behaviour is always ``min(persona, phase/trust)``.
_PERSONA_SESSIONS: dict[ActivityPersona, tuple[int, int]] = {
    "calm": (2, 4),
    "normal": (5, 8),
    "active": (10, 14),
}
_PERSONA_REACTION_PROBABILITY: dict[ActivityPersona, float] = {
    "calm": 0.15,
    "normal": 0.40,
    "active": 0.70,
}
_PERSONA_DM_PROBABILITY: dict[ActivityPersona, float] = {
    "calm": 0.10,
    "normal": 0.30,
    "active": 0.55,
}
# Rough action count of one session (set_online + 1-3 read/react + maybe a DM/
# story). Used to cap sessions/day by the phase daily budget: a young account
# whose cap affords only K sessions runs K, not the persona's headline count.
_EXPECTED_ACTIONS_PER_SESSION = 5


def persona_reaction_probability(persona: ActivityPersona) -> float:
    """Per-session probability the account reacts to a post it read."""
    return _PERSONA_REACTION_PROBABILITY[persona]


def persona_dm_probability(persona: ActivityPersona) -> float:
    """Per-session probability the account starts an inter-account DM.

    Layered on top of the age + trust + settings DM gate — the persona only
    decides *how often* to chat once chatting is already allowed.
    """
    return _PERSONA_DM_PROBABILITY[persona]


def _active_window_hours() -> float:
    """Length of the account's daytime activity window, in hours (24 if disabled)."""
    warm = settings.warming
    if not warm.active_hours_enabled or warm.active_hours_start == warm.active_hours_end:
        return 24.0
    start, end = warm.active_hours_start, warm.active_hours_end
    span = (end - start) if end > start else (24 - start + end)
    return float(span)


def persona_next_run_seconds(
    persona: ActivityPersona,
    daily_cap: int,
    rng: Random,
) -> float:
    """Seconds until the next session, spreading the persona's sessions evenly.

    The active window is split into ``effective_sessions`` equal gaps, where
    ``effective_sessions = min(persona draw, sessions the phase cap affords)`` —
    so the phase ceiling throttles the cadence for young accounts. The gap gets
    ±``next_run_jitter_fraction`` so runs don't land on a rigid grid. No front-
    loading: one gap is returned per finished cycle.
    """
    warm = settings.warming
    low, high = _PERSONA_SESSIONS[persona]
    draw = rng.randint(low, high)
    affordable = max(1, daily_cap // _EXPECTED_ACTIONS_PER_SESSION) if daily_cap > 0 else draw
    sessions = max(1, min(draw, affordable))
    gap_hours = _active_window_hours() / sessions
    jitter = 1.0 + rng.uniform(-warm.next_run_jitter_fraction, warm.next_run_jitter_fraction)
    return max(0.0, gap_hours * jitter) * _SECONDS_PER_HOUR


def _phase_from_age(age_hours: float) -> WarmingPhase:
    """Phase by calendar age alone, ignoring trust."""
    if age_hours < _PHASE_HARD_FLOOR_AGE_HOURS:
        return "intro"
    days = age_hours / 24.0
    for phase in _PHASE_ORDER:
        bound = _PHASE_DAY_BOUND[phase]
        if bound is None or days <= bound:
            return phase
    return "warmed"


def _phase_cap_by_trust(trust_band: str | None) -> WarmingPhase:
    """The highest phase allowed for the given trust band."""
    if trust_band is None:
        return "warmed"
    return _TRUST_PHASE_CEILING.get(trust_band, "warmed")


def effective_phase(age_hours: float, trust_band: str | None) -> WarmingPhase:
    """Min of (age-phase, trust-ceiling) — the safer of the two signals."""
    age_phase = _phase_from_age(age_hours)
    ceiling = _phase_cap_by_trust(trust_band)
    age_rank = _PHASE_ORDER.index(age_phase)
    ceiling_rank = _PHASE_ORDER.index(ceiling)
    return _PHASE_ORDER[min(age_rank, ceiling_rank)]


def _phase_progress(
    phase: WarmingPhase,
    age_hours: float,
) -> tuple[float | None, int | None]:
    """How far through ``phase`` the account is, plus whole days until next.

    ``(progress, days_to_next)`` — both ``None`` for the terminal ``warmed``
    phase, since there is no next boundary.
    """
    bound = _PHASE_DAY_BOUND[phase]
    if bound is None:
        return None, None
    idx = _PHASE_ORDER.index(phase)
    prev_bound = _PHASE_DAY_BOUND[_PHASE_ORDER[idx - 1]] if idx > 0 else None
    # Match ``_phase_from_age``: phase P occupies ``(prev_bound, bound]`` in days
    # (``intro`` starts at 0). The lower edge is exclusive, so the start day is
    # ``prev_bound`` itself — not ``prev_bound + 1``, which zeroed progress for
    # the whole first day of each phase and over-reported ``days_to_next`` by one.
    phase_start_days = 0 if prev_bound is None else prev_bound
    days = age_hours / 24.0
    span = max(1, bound - phase_start_days)
    raw_progress = min(1.0, max(0.0, (days - phase_start_days) / span))
    # Quantise to 1% — the progress bar's smallest visible step is far
    # coarser than the µs drift introduced by recomputing from ``datetime.now()``
    # every 4-second board poll. Without this the per-card signature would
    # flip on every tick and the DOM would rebuild for an idle account.
    progress = round(raw_progress, 2)
    # The account stays in the phase while ``days <= bound`` (the flip is at
    # ``days > bound``), so whole days remaining to the flip is ``bound - int(days)``.
    days_to_next = max(0, bound - int(days))
    return progress, days_to_next


def compute_intensity(
    age_hours: float,
    trust_band: str | None = None,
) -> WarmingIntensity:
    """Map an account's age + trust band to its safety ceiling for one cycle.

    Returns the lifecycle phase + daily action cap (from
    ``effective_phase(age, trust_band)``), the configured session size, and
    whether DMs are permitted (age ≥ ``dm_min_age_hours`` AND the trust band
    allows it). Per-session *frequency* (reactions/DM) is the persona's job, not
    this ceiling's — see ``persona_reaction_probability`` /
    ``persona_dm_probability``.
    """
    warm = settings.warming
    phase = effective_phase(age_hours, trust_band)
    progress, days_to_next = _phase_progress(phase, age_hours)
    if _PHASE_ORDER.index(_phase_cap_by_trust(trust_band)) <= _PHASE_ORDER.index(
        _phase_from_age(age_hours),
    ):
        # Phase is pinned at (or below) the trust ceiling: the age-based progress
        # would point at a next phase the trust band won't permit — a permanent
        # 100%/"0 д", or a steady countdown to an unreachable promotion while the
        # account sits at the top of its ceiling phase. Hide the milestone.
        progress, days_to_next = None, None
    # П11: DM is gated by trust band (when known) on top of the age gate.
    dm_band_ok = trust_band is None or trust_band in _DM_ALLOWED_BANDS
    return WarmingIntensity(
        channels_min=warm.channels_per_cycle_min,
        channels_max=warm.channels_per_cycle_max,
        reaction_probability=warm.reaction_probability,
        dm_allowed=(age_hours >= warm.dm_min_age_hours) and dm_band_ok,
        daily_cap=_PHASE_DAILY_CAP[phase],
        phase=phase,
        progress_to_next=progress,
        days_to_next_phase=days_to_next,
    )


async def _account_tz(account_id: str) -> str | None:
    """The account's IANA timezone (from its phone number), or ``None``."""
    account = await fetch_account(account_id)
    return timezone_for_phone(account.phone) if account else None


def _shift_to_active_hours(candidate: datetime, tz_name: str | None) -> datetime:
    """Move a next-run time into the account's active local window if it's outside.

    Keeps activity clustered in waking hours (account's phone timezone) instead
    of firing uniformly through the night. A ``start == end`` window disables it.
    """
    warm = settings.warming
    if not warm.active_hours_enabled or warm.active_hours_start == warm.active_hours_end:
        return candidate
    if tz_name is None:
        local = candidate.astimezone(UTC)
    else:
        try:
            local = candidate.astimezone(ZoneInfo(tz_name))
        except ZoneInfoNotFoundError:
            local = candidate.astimezone(UTC)
    if _in_quiet_hours(local, warm.active_hours_start, warm.active_hours_end):
        return candidate
    target = local.replace(hour=warm.active_hours_start, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target.astimezone(UTC)
