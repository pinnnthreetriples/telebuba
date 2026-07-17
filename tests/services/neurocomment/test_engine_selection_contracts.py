"""Selection and quota contracts at exact boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from schemas.accounts import AccountRead
from schemas.neurocomment import NeurocommentSettings
from services.neurocomment import _state, engine

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _limits(*, hourly: int = 5, daily: int = 3) -> NeurocommentSettings:
    return NeurocommentSettings(
        max_comments_per_hour=hourly,
        max_comments_per_channel_per_day=daily,
        reply_delay_min_seconds=0,
        reply_delay_max_seconds=1,
        min_trust_score=0,
        updated_at="2026-01-01T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("hourly", "daily", "expected"),
    [
        (4, 2, None),
        (5, 2, "quota_hour"),
        (6, 3, "quota_hour"),
        (4, 3, "quota_day"),
    ],
)
def test_quota_boundaries(hourly: int, daily: int, expected: str | None) -> None:
    assert (
        engine._quota_block_reason("account", _limits(), {"account": hourly}, {"account": daily})
        == expected
    )


def test_zero_daily_cap_is_an_off_switch() -> None:
    assert (
        engine._quota_block_reason("account", _limits(daily=0), {"account": 0}, {"account": 999})
        is None
    )


def _pool() -> engine._SelectionPool:
    account = AccountRead(
        account_id="account",
        status="alive",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    return engine._SelectionPool(
        accounts={"account": account},
        ready_account_ids=frozenset({"account"}),
        states={},
        spam={},
        fingerprints={},
        hourly_counts={},
        daily_counts={},
        limits=_limits(),
    )


def test_block_ladder_stops_at_cooldown_before_other_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pool()._replace(accounts={}, ready_account_ids=frozenset())
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: True)

    assert (
        engine._account_block_reason("account", "@channel", 1, datetime.now(UTC), pool)
        == "cooldown"
    )


def test_missing_account_is_not_ready_even_if_readiness_row_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _pool()._replace(accounts={})
    monkeypatch.setattr(_state, "in_cooldown", lambda *_a: False)

    assert (
        engine._account_block_reason("account", "@channel", 1, datetime.now(UTC), pool)
        == "not_ready"
    )


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ({"not_ready", "unhealthy"}, "unhealthy"),
        ({"cooldown", "quota_day"}, "quota_day"),
        ({"quota_hour", "quota_day"}, "quota_hour"),
    ],
)
def test_selection_miss_reports_highest_priority_blocker(
    monkeypatch: pytest.MonkeyPatch, reasons: set[str], expected: str
) -> None:
    accounts = sorted(reasons)
    monkeypatch.setattr(
        engine,
        "_account_block_reason",
        lambda account_id, *_args: account_id,
    )

    assert (
        engine._selection_block_reason(accounts, "@channel", 1, datetime.now(UTC), _pool())
        == expected
    )


def test_account_lock_is_stable_per_account_and_distinct_between_accounts() -> None:
    first = engine._account_lock("a")
    assert engine._account_lock("a") is first
    assert engine._account_lock("b") is not first
