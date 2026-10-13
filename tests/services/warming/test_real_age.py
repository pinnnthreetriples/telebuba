"""Real account age (item 4.8): earlier DMs past half a year, «warming» on day 2 past a year.

The first-day floor and the trust ceiling always apply on top.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from schemas.accounts import AccountRead
from schemas.warming import WarmingIntensity, WarmingReadiness, WarmingSettings
from services.warming._console import dm_status
from services.warming._real_age import dm_open_after_hours, registered_days
from services.warming.pacing import _account_age_hours, compute_intensity

_NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


def _account(age_hours: float, registered_days_ago: int | None) -> AccountRead:
    created = (_NOW - timedelta(hours=age_hours)).isoformat()
    registered = (
        (_NOW - timedelta(days=registered_days_ago)).date().isoformat()
        if registered_days_ago is not None
        else None
    )
    return AccountRead(
        account_id="acc-1",
        status="alive",
        created_at=created,
        updated_at=created,
        telegram_registered_at=registered,
        registered_source="operator" if registered else None,
    )


def _phase(account: AccountRead, trust_band: str | None = None) -> str:
    return compute_intensity(_account_age_hours(account, _NOW), trust_band, now=_NOW).phase


def test_a_year_old_registration_starts_warming_on_day_two() -> None:
    assert _phase(_account(30, 400)) == "warming"
    # Without the date the same app age is still settling.
    assert _phase(_account(30, None)) == "settling"
    assert _phase(_account(30, 200)) == "settling"


def test_day_one_stays_intro_whatever_the_registration() -> None:
    assert _phase(_account(5, 2000)) == "intro"


def test_the_trust_ceiling_still_caps_an_old_registration() -> None:
    assert _phase(_account(30, 2000), trust_band="critical") == "settling"


def test_an_age_already_past_the_shift_is_kept() -> None:
    assert _account_age_hours(_account(40 * 24, 400), _NOW) == pytest.approx(40 * 24)


def test_dms_open_a_day_earlier_past_half_a_year() -> None:
    warm = settings.warming
    old = _account(30, 200).telegram_registered_at
    young = _account(30, 100).telegram_registered_at
    assert dm_open_after_hours(old, _NOW) == warm.dm_min_age_hours - 24
    assert dm_open_after_hours(young, _NOW) == warm.dm_min_age_hours
    assert dm_open_after_hours(None, _NOW) == warm.dm_min_age_hours

    started = (_NOW - timedelta(hours=warm.dm_min_age_hours - 12)).isoformat()
    assert compute_intensity(
        200.0, "good", now=_NOW, started_at=started, registered_at=old
    ).dm_allowed
    assert not compute_intensity(
        200.0, "good", now=_NOW, started_at=started, registered_at=young
    ).dm_allowed


def test_the_card_names_too_young_against_the_shortened_window() -> None:
    masked = WarmingSettings(
        inter_account_chat=True, has_gemini_key=True, gemini_model="g", updated_at="t"
    )
    closed = WarmingIntensity(
        channels_min=1, channels_max=1, reaction_probability=0, dm_allowed=False
    )
    started = (_NOW - timedelta(hours=settings.warming.dm_min_age_hours - 12)).isoformat()
    ready = WarmingReadiness(ready=True)
    old = _account(30, 200).telegram_registered_at
    assert dm_status(masked, closed, ready, started, _NOW) == "too_young"
    assert dm_status(masked, closed, ready, started, _NOW, old) == "trust"


def test_an_unreadable_date_is_unknown() -> None:
    assert registered_days("not-a-date", _NOW) is None
    assert registered_days(None, _NOW) is None
