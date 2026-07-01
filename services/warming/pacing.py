"""Pure pacing helpers for the warming engine — no Telegram/Gemini I/O.

Scheduling math (quiet hours, daily budget, next-run timing), human-like delays,
the age→intensity ramp, FloodWait classification, and account-local-time helpers.
Kept dependency-light so the engine and the board can both import them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.config import settings
from core.db import fetch_account
from core.phone_geo import timezone_for_phone
from schemas.warming import WarmingIntensity, WarmingPhase, WarmingReadiness

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.spam_status import SpamStatusVerdict
    from schemas.telegram_actions import ActionResult
    from schemas.trust import TrustScore
    from schemas.warming import WarmingStateRecord

_SECONDS_PER_HOUR = 3600

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


def _quiet_hours_end_at(now: datetime, end_hour: int) -> datetime:
    """The next datetime at ``end_hour:00`` strictly after ``now`` (in ``now``'s tz)."""
    candidate = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


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


def warming_days_since(started_at: str | None, now: datetime) -> int | None:
    """Whole days since ``started_at`` (ISO-8601); ``None`` when never started.

    Shared by the board card (the "в прогреве N дн" hint) and the loop's
    target-reached gate (auto-complete once N ≥ the operator's chosen duration).
    """
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, int((now - started).total_seconds() / _SECONDS_PER_HOUR // 24))


def _account_age_hours(account: AccountRead | None, now: datetime) -> float:
    """Hours since the account was created; full-ramp age when unknown.

    A missing/unparseable ``created_at`` degrades to full intensity so an
    anomalous record never silently freezes an account at day-one behaviour.
    """
    if account is None:
        return settings.warming.ramp_full_age_hours
    try:
        created = datetime.fromisoformat(account.created_at)
    except ValueError:
        return settings.warming.ramp_full_age_hours
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
    "intro": 2,
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

# Hard age floor: fresh accounts (< 72 hours) cannot exceed ``intro`` even with
# a clean trust score and clean proxy. Sources unanimous: the first 72 h are
# the highest-risk window regardless of other signals.
_PHASE_HARD_FLOOR_AGE_HOURS = 72.0

# Trust bands permitted to send DMs (audit П11). DM is the highest-risk action,
# so ``at_risk``/``critical`` accounts are blocked even once old enough.
_DM_ALLOWED_BANDS: Final = frozenset({"excellent", "good", "watch"})


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
    phase_start_days = 0 if prev_bound is None else prev_bound + 1
    days = age_hours / 24.0
    span = max(1, bound - phase_start_days + 1)
    raw_progress = min(1.0, max(0.0, (days - phase_start_days) / span))
    # Quantise to 1% — the progress bar's smallest visible step is far
    # coarser than the µs drift introduced by recomputing from ``datetime.now()``
    # every 4-second board poll. Without this the per-card signature would
    # flip on every tick and the DOM would rebuild for an idle account.
    progress = round(raw_progress, 2)
    days_to_next = max(0, bound + 1 - int(days))
    return progress, days_to_next


def compute_intensity(
    age_hours: float,
    trust_band: str | None = None,
) -> WarmingIntensity:
    """Map an account's age + trust band to its per-cycle intensity and cap.

    Channels-per-cycle and reaction rate grow linearly from a quiet initial
    floor to the configured full values over ``ramp_full_age_hours``; DM is
    gated until ``dm_min_age_hours``. The lifecycle phase + daily cap are
    derived from ``effective_phase(age, trust_band)``. With the legacy ramp
    disabled, channels/reactions/DM still get full intensity, but the phase
    machinery still applies the daily cap.
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
    daily_cap = _PHASE_DAILY_CAP[phase]
    # П11: DM is gated by trust band (when known) on top of the age gate below.
    dm_band_ok = trust_band is None or trust_band in _DM_ALLOWED_BANDS
    if not warm.ramp_enabled:
        return WarmingIntensity(
            channels_min=warm.channels_per_cycle_min,
            channels_max=warm.channels_per_cycle_max,
            reaction_probability=warm.reaction_probability,
            dm_allowed=dm_band_ok,
            daily_cap=daily_cap,
            phase=phase,
            progress_to_next=progress,
            days_to_next_phase=days_to_next,
        )
    if warm.ramp_full_age_hours <= 0:
        frac = 1.0
    else:
        frac = min(1.0, max(0.0, age_hours / warm.ramp_full_age_hours))
    initial_channels = min(warm.ramp_initial_channels_max, warm.channels_per_cycle_max)
    grown = round(frac * (warm.channels_per_cycle_max - initial_channels))
    channels_max = max(1, initial_channels + grown)
    channels_min = min(warm.channels_per_cycle_min, channels_max)
    reaction_probability = warm.ramp_initial_reaction_probability + frac * (
        warm.reaction_probability - warm.ramp_initial_reaction_probability
    )
    return WarmingIntensity(
        channels_min=channels_min,
        channels_max=channels_max,
        reaction_probability=min(1.0, max(0.0, reaction_probability)),
        dm_allowed=(age_hours >= warm.dm_min_age_hours) and dm_band_ok,
        daily_cap=daily_cap,
        phase=phase,
        progress_to_next=progress,
        days_to_next_phase=days_to_next,
    )


async def _account_tz(account_id: str) -> str | None:
    """The account's IANA timezone (from its phone number), or ``None``."""
    account = await fetch_account(account_id)
    return timezone_for_phone(account.phone) if account else None


async def _local_now(account_id: str, now: datetime) -> datetime:
    """Return ``now`` in the account's local timezone (from its phone number).

    Quiet hours are evaluated in the account's local time rather than UTC. Falls
    back to ``now`` when the number has no resolvable timezone.
    """
    tz_name = await _account_tz(account_id)
    if tz_name is None:
        return now
    try:
        return now.astimezone(ZoneInfo(tz_name))
    except ZoneInfoNotFoundError:
        return now


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
