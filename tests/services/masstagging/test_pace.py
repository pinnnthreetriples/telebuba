"""The arithmetic of one account's day: Premium ceilings, the soft start, the 09:00 day."""

from __future__ import annotations

from datetime import datetime

import pytest

from schemas.masstagging import MasstaggingPace
from schemas.masstagging_records import AccountDay
from services.masstagging.pace import (
    AccountView,
    batch_size,
    caption_limit,
    daily_reach,
    day_of,
    gap_seconds,
    in_ramp,
    left_today,
    lifetime_seconds,
    next_day_start,
)


def _view(*, premium: bool = True, ramp: bool = False, **day: int) -> AccountView:
    return AccountView(
        account_id="a1",
        premium=premium,
        day=AccountDay(account_id="a1", day="2026-10-11", **day),
        ramp=ramp,
    )


def _local(hour: int, minute: int = 0, day: int = 11) -> int:
    return int(datetime(2026, 10, day, hour, minute).astimezone().timestamp())


def test_the_day_starts_at_nine_local_time() -> None:
    assert day_of(_local(8, 59)) == "2026-10-10"
    assert day_of(_local(9, 0)) == "2026-10-11"
    assert next_day_start(_local(8, 30)) == _local(9)
    assert next_day_start(_local(9, 0)) == _local(9, day=12)
    assert next_day_start(_local(23, 0)) == _local(9, day=12)


def test_premium_reach_is_stories_times_mentions() -> None:
    pace = MasstaggingPace(stories_per_day=15, mentions_per_story=5)
    assert daily_reach(_view(), "stories", pace) == 75


def test_a_free_account_gets_one_story_of_at_most_five_mentions() -> None:
    pace = MasstaggingPace(stories_per_day=15, mentions_per_story=8)
    view = _view(premium=False)
    assert daily_reach(view, "stories", pace) == 5
    assert batch_size(view, "stories", pace) == 5
    assert left_today(_view(premium=False, stories=1), "stories", pace) == 0


def test_the_soft_start_caps_the_day_and_lengthens_the_pause() -> None:
    pace = MasstaggingPace(stories_per_day=15, mentions_per_story=5, dialogs_per_day=40)
    view = _view(ramp=True)
    assert daily_reach(view, "stories", pace) == 6
    assert daily_reach(view, "priming", pace) == 6
    assert 200 <= gap_seconds(view, "priming", pace, 0.0) <= 600
    assert gap_seconds(view, "priming", pace, 1.0) == 600


def test_priming_reach_is_the_people_a_day() -> None:
    pace = MasstaggingPace(dialogs_per_day=6)
    assert daily_reach(_view(premium=False), "priming", pace) == 6
    assert batch_size(_view(), "priming", pace) == 1
    assert left_today(_view(tags=6), "priming", pace) == 0


def test_lookups_cap_the_day_whatever_the_pace() -> None:
    pace = MasstaggingPace(stories_per_day=100, mentions_per_story=10)
    assert daily_reach(_view(), "stories", pace) == 200
    assert left_today(_view(resolves=200), "stories", pace) == 0


def test_floods_in_a_row_end_the_day() -> None:
    pace = MasstaggingPace(floods_to_rest=3)
    assert left_today(_view(floods_in_row=2), "priming", pace) > 0
    assert left_today(_view(floods_in_row=3), "priming", pace) == 0


@pytest.mark.parametrize(
    ("first", "today", "expected"),
    [
        (None, "2026-10-11", True),
        ("2026-10-09", "2026-10-11", True),
        ("2026-10-08", "2026-10-11", False),
    ],
)
def test_the_soft_start_lasts_three_days(first: str | None, today: str, *, expected: bool) -> None:
    assert in_ramp(first, today, enabled=True) is expected
    assert in_ramp(first, today, enabled=False) is False


def test_free_accounts_get_the_short_caption_and_lifetime() -> None:
    assert caption_limit(_view(premium=False)) == 200
    assert caption_limit(_view()) == 2048
    assert lifetime_seconds(_view(premium=False), 48) == 24 * 3600
    assert lifetime_seconds(_view(), 6) == 6 * 3600


def test_the_pause_is_drawn_from_the_campaign_range() -> None:
    pace = MasstaggingPace.model_validate(
        {"story_gap_minutes": {"min": 1, "max": 2}, "dialog_gap_seconds": {"min": 20, "max": 60}}
    )
    assert gap_seconds(_view(), "stories", pace, 0.5) == 90
    assert gap_seconds(_view(), "priming", pace, 0.0) == 20
