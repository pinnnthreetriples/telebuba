"""How much one account may do: the masstagging day, Premium ceilings, the soft start.

Pure functions over the account's counters and the campaign's «Темп», so the board
estimates and the engine decide with the same arithmetic.

A masstagging day starts at :data:`DAY_START_HOUR` local server time: an account that
spent its day rests until then.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingMode, MasstaggingPace
    from schemas.masstagging_records import AccountDay

DAY_START_HOUR = 9
_HOUR = 3600


def day_of(unix: int) -> str:
    """The masstagging day ``unix`` falls in, as ``YYYY-MM-DD`` of the day it began."""
    local = datetime.fromtimestamp(unix).astimezone()  # local on purpose
    return (local - timedelta(hours=DAY_START_HOUR)).date().isoformat()


def next_day_start(unix: int) -> int:
    """When the next masstagging day begins after ``unix``."""
    local = datetime.fromtimestamp(unix).astimezone()  # local on purpose
    start = local.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    if start <= local:
        start += timedelta(days=1)
    return int(start.timestamp())


@dataclass(frozen=True)
class AccountView:
    """What the limits read of one account."""

    account_id: str
    premium: bool
    day: AccountDay
    # Still in its first days of masstagging (the soft start applies).
    ramp: bool


def in_ramp(first_day: str | None, today: str, *, enabled: bool) -> bool:
    days = settings.masstagging.ramp_days
    if not enabled or days <= 0:
        return False
    if first_day is None:
        return True
    return (date.fromisoformat(today) - date.fromisoformat(first_day)).days < days


def stories_cap(view: AccountView, pace: MasstaggingPace) -> int:
    limits = settings.masstagging
    if not view.premium:
        return limits.free_stories_per_day
    return min(pace.stories_per_day, limits.premium_stories_per_day)


def mentions_cap(view: AccountView, pace: MasstaggingPace) -> int:
    if view.premium:
        return pace.mentions_per_story
    return min(pace.mentions_per_story, settings.masstagging.free_mentions_per_story)


def daily_reach(view: AccountView, mode: MasstaggingMode, pace: MasstaggingPace) -> int:
    """People this account may reach in a whole day."""
    if mode == "priming":
        reach = pace.dialogs_per_day
    else:
        reach = stories_cap(view, pace) * mentions_cap(view, pace)
    if view.ramp:
        reach = min(reach, settings.masstagging.ramp_daily_cap)
    return min(reach, settings.masstagging.resolves_per_day)


def left_today(view: AccountView, mode: MasstaggingMode, pace: MasstaggingPace) -> int:
    """People it may still reach today; 0 = it rests until tomorrow."""
    day = view.day
    if day.floods_in_row >= pace.floods_to_rest:
        return 0
    if day.resolves >= settings.masstagging.resolves_per_day:
        return 0
    if mode == "stories" and day.stories >= stories_cap(view, pace):
        return 0
    return max(0, daily_reach(view, mode, pace) - day.tags)


def batch_size(view: AccountView, mode: MasstaggingMode, pace: MasstaggingPace) -> int:
    """How many targets the next step takes: one story's mentions, or one person."""
    left = left_today(view, mode, pace)
    if mode == "priming":
        return min(1, left)
    return min(mentions_cap(view, pace), left)


def gap_seconds(
    view: AccountView, mode: MasstaggingMode, pace: MasstaggingPace, draw: float
) -> int:
    """The pause after one step; ``draw`` is a uniform 0..1 from the caller's seam."""
    limits = settings.masstagging
    if view.ramp:
        low, high = limits.ramp_pause_min_seconds, limits.ramp_pause_max_seconds
    elif mode == "stories":
        low, high = pace.story_gap_minutes.min * 60, pace.story_gap_minutes.max * 60
    else:
        low, high = pace.dialog_gap_seconds.min, pace.dialog_gap_seconds.max
    return int(low + (high - low) * draw)


def caption_limit(view: AccountView) -> int:
    limits = settings.masstagging
    return limits.premium_caption_chars if view.premium else limits.free_caption_chars


def lifetime_seconds(view: AccountView, hours: int) -> int:
    """A free account's story lives 24 h whatever the campaign asks."""
    return hours * _HOUR if view.premium else 24 * _HOUR
