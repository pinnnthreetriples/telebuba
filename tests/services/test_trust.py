"""Tests for ``services.trust`` — the internal account Trust Score."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, update_account_from_session_check
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.trust import TrustSignals
from services.accounts import add_account
from services.trust import account_trust_score, compute_trust_score

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def test_compute_trust_score_healthy_account_is_excellent() -> None:
    score = compute_trust_score(
        TrustSignals(
            account_id="a",
            account_status="alive",
            spam_status="clean",
            quarantine_count=0,
            flood_active=False,
            geo_status="match",
            proxy_status="tcp_working",
            age_hours=1000.0,
        ),
    )
    assert score.score == 100
    assert score.band == "excellent"
    assert score.reasons == []


def test_compute_trust_score_spam_limited_drops_band() -> None:
    score = compute_trust_score(
        TrustSignals(
            account_id="a",
            account_status="alive",
            spam_status="limited",
            quarantine_count=0,
            flood_active=False,
            geo_status="match",
            proxy_status="tcp_working",
            age_hours=1000.0,
        ),
    )
    assert score.score == 50  # 100 - penalty_spam_limited
    assert score.band == "at_risk"
    assert "spam-limited" in score.reasons


def test_compute_trust_score_clamps_at_zero() -> None:
    score = compute_trust_score(
        TrustSignals(
            account_id="a",
            account_status="banned",
            spam_status="limited",
            quarantine_count=3,
            flood_active=True,
            geo_status="mismatch",
            proxy_status="failed",
            age_hours=0.0,
        ),
    )
    assert score.score == 0
    assert score.band == "critical"


@pytest.mark.asyncio
async def test_account_trust_score_for_fresh_alive_account() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1",
            session_path="acc-1",
            status="alive",
            is_temporary=False,
        ),
    )

    score = await account_trust_score("acc-1")

    # alive(0) - spam unknown(10) - geo unknown(5) - new account(10) = 75
    assert score.score == 75
    assert score.band == "good"


@pytest.mark.asyncio
async def test_account_trust_score_unknown_account_is_critical() -> None:
    score = await account_trust_score("ghost")
    assert score.score == 0
    assert score.band == "critical"
