"""Boundary and dependency contracts for the public Trust Score API."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.config import settings
from schemas.accounts import AccountRead
from schemas.spam_status import SpamStatusVerdict
from schemas.trust import TrustScore, TrustSignals
from schemas.warming import WarmingStateRecord
from services import trust


def healthy_signals(*, account_status: str = "alive") -> TrustSignals:
    return TrustSignals(
        account_id="acc-trust",
        account_status=account_status,
        spam_status="clean",
        quarantine_count=0,
        flood_active=False,
        geo_status="match",
        proxy_status="tcp_working",
        age_hours=1_000.0,
    )


def clean_spam(account_id: str) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id,
        status="clean",
        checked_at="2026-07-18T12:00:00+00:00",
    )


def warming_record(account_id: str, flood_wait_until: str) -> WarmingStateRecord:
    return WarmingStateRecord(
        account_id=account_id,
        state="flood_wait",
        updated_at="2026-07-18T12:00:00+00:00",
        flood_wait_until=flood_wait_until,
    )


@pytest.mark.parametrize(
    ("penalty", "expected_score", "expected_band"),
    [
        (10, 90, "excellent"),
        (25, 75, "good"),
        (40, 60, "watch"),
        (60, 40, "at_risk"),
        (61, 39, "critical"),
    ],
)
def test_compute_trust_score_exposes_every_configured_band_boundary(
    monkeypatch: pytest.MonkeyPatch,
    penalty: int,
    expected_score: int,
    expected_band: str,
) -> None:
    monkeypatch.setattr(settings.trust, "penalty_not_alive", penalty)

    result = trust.compute_trust_score(healthy_signals(account_status="unauthorized"))

    assert result.score == expected_score
    assert result.band == expected_band
    assert result.reasons == ["status unauthorized"]


def test_compute_trust_score_applies_each_independent_penalty_and_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.trust, "penalty_not_alive", 2)
    monkeypatch.setattr(settings.trust, "penalty_spam_limited", 3)
    monkeypatch.setattr(settings.trust, "penalty_quarantine_each", 5)
    monkeypatch.setattr(settings.trust, "penalty_flood_active", 7)
    monkeypatch.setattr(settings.trust, "penalty_geo_mismatch", 11)
    monkeypatch.setattr(settings.trust, "penalty_proxy_failed", 13)
    monkeypatch.setattr(settings.trust, "penalty_new_account", 17)
    monkeypatch.setattr(settings.trust, "new_account_hours", 48.0)

    result = trust.compute_trust_score(
        TrustSignals(
            account_id="acc-risk",
            account_status="flood_wait",
            spam_status="limited",
            quarantine_count=2,
            flood_active=True,
            geo_status="mismatch",
            proxy_status="failed",
            age_hours=0.0,
        ),
    )

    assert result.score == 37
    assert result.reasons == [
        "status flood_wait",
        "spam-limited",
        "quarantined x2",
        "recent flood",
        "geo mismatch",
        "proxy failed",
        "new account",
    ]


def test_single_quarantine_and_unknown_geo_are_visible_risk_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.trust, "penalty_quarantine_each", 9)
    monkeypatch.setattr(settings.trust, "penalty_geo_unknown", 7)

    result = trust.compute_trust_score(
        TrustSignals(
            account_id="acc-one-quarantine",
            account_status="alive",
            spam_status="clean",
            quarantine_count=1,
            flood_active=False,
            geo_status="unknown",
            proxy_status="tcp_working",
            age_hours=1_000.0,
        ),
    )

    assert result.score == 84
    assert result.reasons == ["quarantined x1", "geo unknown"]


@pytest.mark.parametrize(
    ("new_account_hours", "expected_score", "expected_reasons"),
    [(0.0, 100, []), (1.0, 83, ["new account"])],
)
def test_new_account_ramp_honours_configured_zero_and_one_hour_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    new_account_hours: float,
    expected_score: int,
    expected_reasons: list[str],
) -> None:
    monkeypatch.setattr(settings.trust, "new_account_hours", new_account_hours)
    monkeypatch.setattr(settings.trust, "penalty_new_account", 17)
    signals = healthy_signals().model_copy(update={"age_hours": 0.0})

    result = trust.compute_trust_score(signals)

    assert result.score == expected_score
    assert result.reasons == expected_reasons


@pytest.mark.asyncio
async def test_account_trust_score_loads_each_dependency_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: Counter[str] = Counter()
    account = SimpleNamespace(account_id="acc-deps")
    record = SimpleNamespace(name="warming")
    spam = SimpleNamespace(name="spam")
    fingerprint = SimpleNamespace(system_lang_code="en-US")
    expected = TrustScore(account_id="acc-deps", score=81, band="good")
    captured: list[dict[str, object]] = []

    async def fetch_account(account_id: str) -> object:
        calls[f"account:{account_id}"] += 1
        return account

    async def fetch_warming(account_id: str) -> object:
        calls[f"warming:{account_id}"] += 1
        return record

    async def fetch_spam(account_id: str) -> object:
        calls[f"spam:{account_id}"] += 1
        return spam

    async def fetch_fingerprint(account_id: str) -> object:
        calls[f"fingerprint:{account_id}"] += 1
        return fingerprint

    def score_from(**kwargs: object) -> TrustScore:
        calls["score"] += 1
        captured.append(kwargs)
        return expected

    monkeypatch.setattr(trust, "fetch_account", fetch_account)
    monkeypatch.setattr(trust, "fetch_warming_state", fetch_warming)
    monkeypatch.setattr(trust, "get_spam_status", fetch_spam)
    monkeypatch.setattr(trust, "fetch_device_fingerprint", fetch_fingerprint)
    monkeypatch.setattr(trust, "account_trust_score_from", score_from)

    result = await trust.account_trust_score("acc-deps")

    assert result is expected
    assert calls == Counter(
        {
            "account:acc-deps": 1,
            "warming:acc-deps": 1,
            "spam:acc-deps": 1,
            "fingerprint:acc-deps": 1,
            "score": 1,
        },
    )
    assert len(captured) == 1
    assert captured[0]["account"] is account
    assert captured[0]["record"] is record
    assert captured[0]["spam"] is spam
    assert captured[0]["lang_code"] == "en-US"
    assert isinstance(captured[0]["now"], datetime)
    assert captured[0]["now"].tzinfo is UTC


@pytest.mark.parametrize(
    ("created_at", "flood_until", "expected_score", "expected_reasons"),
    [
        (
            "2026-07-16T12:00:00+00:00",
            "2026-07-18T12:00:00+00:00",
            100,
            [],
        ),
        (
            "not-a-date",
            "not-a-date",
            90,
            ["new account"],
        ),
    ],
)
def test_score_from_handles_time_boundaries_and_malformed_persisted_values(
    monkeypatch: pytest.MonkeyPatch,
    created_at: str,
    flood_until: str,
    expected_score: int,
    expected_reasons: list[str],
) -> None:
    monkeypatch.setattr(
        trust,
        "evaluate_geo",
        lambda **_kwargs: SimpleNamespace(status="match"),
    )
    account = AccountRead(
        account_id="acc-time",
        status="alive",
        created_at=created_at,
        updated_at=created_at,
        phone="+12125550100",
        proxy_country_code="US",
        proxy_status="tcp_working",
    )
    record = warming_record(account.account_id, flood_until)
    spam = clean_spam(account.account_id)

    result = trust.account_trust_score_from(
        account=account,
        record=record,
        spam=spam,
        lang_code="en-US",
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )

    assert result.score == expected_score
    assert result.reasons == expected_reasons


def account_for_time_contract(created_at: str, *, proxy_status: str = "tcp_working") -> AccountRead:
    return AccountRead(
        account_id="acc-time-contract",
        status="alive",
        created_at=created_at,
        updated_at=created_at,
        phone="+12125550100",
        proxy_country_code="US",
        proxy_status=proxy_status,
    )


@pytest.mark.parametrize(
    ("created_at", "expected_score"),
    [
        ("not-a-date", 52),
        ("2026-07-17T12:00:00", 76),
        ("2026-07-19T12:00:00+00:00", 52),
    ],
)
def test_score_from_normalizes_malformed_naive_and_future_creation_times(
    monkeypatch: pytest.MonkeyPatch,
    created_at: str,
    expected_score: int,
) -> None:
    monkeypatch.setattr(settings.trust, "new_account_hours", 48.0)
    monkeypatch.setattr(settings.trust, "penalty_new_account", 48)

    result = trust.account_trust_score_from(
        account=account_for_time_contract(created_at),
        record=None,
        spam=clean_spam("acc-time-contract"),
        lang_code="en-US",
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )

    assert result.score == expected_score
    assert result.reasons == ["new account"]


def test_score_from_uses_phone_proxy_flood_and_proxy_health_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.trust, "new_account_hours", 0.0)
    account = account_for_time_contract(
        "2026-07-01T00:00:00+00:00",
        proxy_status="failed",
    )
    record = warming_record(
        account.account_id,
        # 08:30 at UTC-04:00 is 12:30 UTC: still active at the chosen `now`.
        "2026-07-18T08:30:00-04:00",
    )

    result = trust.account_trust_score_from(
        account=account,
        record=record,
        spam=clean_spam(account.account_id),
        lang_code="en-US",
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )

    assert result.score == 65
    assert result.reasons == ["recent flood", "proxy failed"]


def test_score_from_marks_missing_phone_or_proxy_as_geo_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.trust, "new_account_hours", 0.0)
    account = account_for_time_contract("2026-07-01T00:00:00+00:00")

    matched = trust.account_trust_score_from(
        account=account,
        record=None,
        spam=clean_spam(account.account_id),
        lang_code="en-US",
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )
    missing_phone = trust.account_trust_score_from(
        account=account.model_copy(update={"phone": None}),
        record=None,
        spam=clean_spam(account.account_id),
        lang_code="en-US",
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )
    missing_proxy = trust.account_trust_score_from(
        account=account.model_copy(update={"proxy_country_code": None}),
        record=None,
        spam=clean_spam(account.account_id),
        lang_code="en-US",
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )

    assert (matched.score, matched.reasons) == (100, [])
    assert (missing_phone.score, missing_phone.reasons) == (95, ["geo unknown"])
    assert (missing_proxy.score, missing_proxy.reasons) == (95, ["geo unknown"])
