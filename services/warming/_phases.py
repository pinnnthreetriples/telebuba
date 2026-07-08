"""Warming lifecycle phases + activity personas — the safety-ceiling math.

The 5-phase age/trust lifecycle table (per-phase daily action cap, DM gate,
progress-to-next) and the operator-chosen activity-persona presets (target
cadence, per-session reaction/DM probability). Split from ``pacing`` for the
file-size budget; ``pacing`` re-exports every public name so callers keep
importing them from ``services.warming.pacing``.

Dependency-light (only ``core.config`` + ``schemas.warming`` + stdlib) so it
stays a leaf that ``pacing`` and the board/engine can both import.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from core.config import settings
from schemas.warming import (
    ActivityPersona,
    WarmingIntensity,
    WarmingPhase,
)

if TYPE_CHECKING:
    from random import Random

    from schemas.accounts import AccountRead

_SECONDS_PER_HOUR = 3600


def _account_age_hours(account: AccountRead | None, now: datetime) -> float:
    """Hours since the account was created; full-ramp age when unknown.

    A missing/unparseable ``created_at`` degrades to full intensity so an
    anomalous record never silently freezes an account at day-one behaviour.
    """
    fallback = settings.warming.unknown_age_fallback_hours
    if account is None:
        return fallback
    try:
        created = datetime.fromisoformat(account.created_at)
    except ValueError:
        return fallback
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0.0, (now - created).total_seconds() / _SECONDS_PER_HOUR)


# Five-phase lifecycle order — drives the per-account daily action cap and the
# visible "what stage is this account in" affordance on the card. The per-phase
# day bounds and daily caps now live in ``settings.warming`` (config-driven);
# this ordering is structural, not a tunable, so it stays here.
_PHASE_ORDER: tuple[WarmingPhase, ...] = (
    "intro",
    "settling",
    "warming",
    "active",
    "warmed",
)

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

# Trust bands permitted to send DMs (audit П11). DM is the highest-risk action,
# so ``at_risk``/``critical`` accounts are blocked even once old enough.
_DM_ALLOWED_BANDS: Final = frozenset({"excellent", "good", "watch"})


def persona_reaction_probability(persona: ActivityPersona) -> float:
    """Per-session probability the account reacts to a post it read."""
    return settings.warming.persona_reaction_probability[persona]


def persona_dm_probability(persona: ActivityPersona) -> float:
    """Per-session probability the account starts an inter-account DM.

    Layered on top of the age + trust + settings DM gate — the persona only
    decides *how often* to chat once chatting is already allowed.
    """
    return settings.warming.persona_dm_probability[persona]


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
    low, high = warm.persona_sessions[persona]
    draw = rng.randint(low, high)
    affordable = max(1, daily_cap // warm.expected_actions_per_session) if daily_cap > 0 else draw
    sessions = max(1, min(draw, affordable))
    gap_hours = _active_window_hours() / sessions
    jitter = 1.0 + rng.uniform(-warm.next_run_jitter_fraction, warm.next_run_jitter_fraction)
    return max(0.0, gap_hours * jitter) * _SECONDS_PER_HOUR


def _phase_from_age(age_hours: float) -> WarmingPhase:
    """Phase by calendar age alone, ignoring trust."""
    if age_hours < settings.warming.phase_hard_floor_age_hours:
        return "intro"
    day_bound = settings.warming.phase_day_bound
    days = age_hours / 24.0
    for phase in _PHASE_ORDER:
        bound = day_bound[phase]
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
    day_bound = settings.warming.phase_day_bound
    bound = day_bound[phase]
    if bound is None:
        return None, None
    idx = _PHASE_ORDER.index(phase)
    prev_bound = day_bound[_PHASE_ORDER[idx - 1]] if idx > 0 else None
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
        daily_cap=warm.phase_daily_cap[phase],
        phase=phase,
        progress_to_next=progress,
        days_to_next_phase=days_to_next,
    )
