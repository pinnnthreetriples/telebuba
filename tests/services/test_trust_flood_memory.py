"""The flood-memory trust penalty: capped by count, fading over ~30 days."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from schemas.trust import TrustSignals
from services.trust import compute_trust_score

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def _score(flood_total: int, days_ago: float | None) -> int:
    last = None if days_ago is None else (_NOW - timedelta(days=days_ago)).isoformat()
    return compute_trust_score(
        TrustSignals(
            account_id="a",
            account_status="alive",
            spam_status="clean",
            quarantine_count=0,
            flood_active=False,
            geo_status="match",
            proxy_status="tcp_working",
            age_hours=1000.0,
            flood_total=flood_total,
            last_flood_at=last,
        ),
        _NOW,
    ).score


def test_no_floods_cost_nothing() -> None:
    assert _score(0, None) == 100


@pytest.mark.parametrize(("days_ago", "score"), [(0, 80), (15, 90), (30, 100), (45, 100)])
def test_the_penalty_decays_to_zero_over_thirty_days(days_ago: float, score: int) -> None:
    assert _score(2, days_ago) == score


def test_the_penalty_is_capped_however_many_floods() -> None:
    assert _score(10, 0) == 70


def test_an_unreadable_flood_date_costs_nothing() -> None:
    signals = TrustSignals(
        account_id="a",
        account_status="alive",
        spam_status="clean",
        quarantine_count=0,
        flood_active=False,
        geo_status="match",
        age_hours=1000.0,
        flood_total=3,
        last_flood_at="garbage",
    )
    assert compute_trust_score(signals, _NOW).score == 100


def test_the_reason_names_the_flood_count() -> None:
    signals = TrustSignals(
        account_id="a",
        account_status="alive",
        spam_status="clean",
        quarantine_count=0,
        flood_active=False,
        geo_status="match",
        age_hours=1000.0,
        flood_total=2,
        last_flood_at=(_NOW - timedelta(days=1)).isoformat(),
    )
    assert "floods x2" in compute_trust_score(signals, _NOW).reasons
