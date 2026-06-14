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
from schemas.warming import WarmingIntensity, WarmingReadiness

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.telegram_actions import ActionResult
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
    """True when ``now``'s UTC hour falls in the ``[start, end)`` window.

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
    """The next UTC datetime at ``end_hour:00`` strictly after ``now``."""
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


def evaluate_readiness(account: AccountRead, channel_count: int) -> WarmingReadiness:
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


def compute_intensity(age_hours: float) -> WarmingIntensity:
    """Map an account's age to its per-cycle intensity via the configured ramp.

    Channels-per-cycle and reaction rate grow linearly from a quiet initial
    floor to the configured full values over ``ramp_full_age_hours``; DM is
    gated until ``dm_min_age_hours``. With the ramp disabled, every account runs
    at full intensity with DM allowed.
    """
    warm = settings.warming
    if not warm.ramp_enabled:
        return WarmingIntensity(
            channels_min=warm.channels_per_cycle_min,
            channels_max=warm.channels_per_cycle_max,
            reaction_probability=warm.reaction_probability,
            dm_allowed=True,
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
        dm_allowed=age_hours >= warm.dm_min_age_hours,
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
