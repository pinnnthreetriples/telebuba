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
    WarmingReadiness,
    is_warming,
)

# The phase/persona safety-ceiling math lives in ``_phases`` (file-size budget);
# re-exported here so callers keep importing every name from ``pacing``. Seams
# that patch ``services.warming.pacing.<name>`` still resolve through these.
from services.warming._phases import (  # noqa: F401 - re-exported for the pacing public API.
    _DM_ALLOWED_BANDS,
    _PHASE_ORDER,
    _SECONDS_PER_HOUR,
    _TRUST_PHASE_CEILING,
    _account_age_hours,
    _active_window_hours,
    _phase_cap_by_trust,
    _phase_from_age,
    _phase_progress,
    compute_intensity,
    effective_phase,
    persona_dm_probability,
    persona_next_run_seconds,
    persona_reaction_probability,
)

if TYPE_CHECKING:
    from random import Random

    from schemas.accounts import AccountRead
    from schemas.spam_status import SpamStatusVerdict
    from schemas.telegram_actions import ActionResult
    from schemas.trust import TrustScore
    from schemas.warming import WarmingState, WarmingStateRecord

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


async def _account_tz(account_id: str) -> str | None:
    """The account's IANA timezone (from its phone number), or ``None``."""
    account = await fetch_account(account_id)
    return timezone_for_phone(account.phone) if account else None


def _shift_to_active_hours(candidate: datetime, tz_name: str | None, rng: Random) -> datetime:
    """Move a next-run time into the account's active local window if it's outside.

    Keeps activity clustered in waking hours (account's phone timezone) instead
    of firing uniformly through the night. A ``start == end`` window disables it.

    A shifted resume lands at a random point in ``[start, start + spread)`` rather
    than exactly ``HH:00:00`` — an unseeded ``rng`` per call de-correlates accounts
    so a fleet that parked overnight does not all wake on the same wall-clock second.
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
    target += timedelta(seconds=rng.uniform(0, warm.active_hours_start_spread_minutes * 60))
    return target.astimezone(UTC)
