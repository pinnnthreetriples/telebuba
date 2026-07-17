"""Bulk account-health and quota selection contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from schemas.accounts import AccountList, AccountRead
from schemas.device_fingerprint import DeviceFingerprint
from schemas.neurocomment import NeurocommentSettings
from schemas.spam_status import SpamStatusVerdict
from schemas.warming import WarmingStateRecord
from services.neurocomment import engine

pytestmark = pytest.mark.usefixtures("isolate_engine")


def _account(account_id: str) -> AccountRead:
    return AccountRead(
        account_id=account_id,
        status="alive",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _limits(*, daily: int) -> NeurocommentSettings:
    return NeurocommentSettings(
        max_comments_per_hour=5,
        max_comments_per_channel_per_day=daily,
        reply_delay_min_seconds=0,
        reply_delay_max_seconds=1,
        min_trust_score=50,
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _patch_pool_reads(
    monkeypatch: pytest.MonkeyPatch, *, daily_cap: int
) -> tuple[AsyncMock, AsyncMock]:
    monkeypatch.setattr(
        engine, "load_neuro_settings", AsyncMock(return_value=_limits(daily=daily_cap))
    )
    monkeypatch.setattr(
        engine, "list_accounts", AsyncMock(return_value=AccountList(accounts=[_account("a")]))
    )
    monkeypatch.setattr(
        engine,
        "list_campaign_readiness",
        AsyncMock(
            return_value=SimpleNamespace(
                readiness=[
                    SimpleNamespace(
                        account_id="a",
                        channel="@channel",
                        ready=True,
                        human_skipped=False,
                        banned=False,
                    ),
                    SimpleNamespace(
                        account_id="skipped",
                        channel="@channel",
                        ready=True,
                        human_skipped=True,
                        banned=False,
                    ),
                    SimpleNamespace(
                        account_id="banned",
                        channel="@channel",
                        ready=True,
                        human_skipped=False,
                        banned=True,
                    ),
                    SimpleNamespace(
                        account_id="other",
                        channel="@other",
                        ready=True,
                        human_skipped=False,
                        banned=False,
                    ),
                ]
            )
        ),
    )
    monkeypatch.setattr(engine, "list_warming_states", AsyncMock(return_value=[]))
    monkeypatch.setattr(engine, "list_spam_statuses", AsyncMock(return_value={}))
    monkeypatch.setattr(engine, "list_device_fingerprints", AsyncMock(return_value={}))
    hourly = AsyncMock(return_value=SimpleNamespace(counts=[]))
    daily = AsyncMock(return_value=SimpleNamespace(counts=[]))
    monkeypatch.setattr(engine, "count_comments_per_account_since", hourly)
    monkeypatch.setattr(engine, "count_channel_comments_per_account_since", daily)
    return hourly, daily


@pytest.mark.asyncio
async def test_pool_excludes_skipped_banned_and_other_channel_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hourly, daily = _patch_pool_reads(monkeypatch, daily_cap=2)
    now = datetime(2026, 2, 3, 12, 0, tzinfo=UTC)

    pool = await engine._load_selection_pool("campaign", "@channel", now)

    assert pool.ready_account_ids == frozenset({"a"})
    hourly.assert_awaited_once_with("2026-02-03T11:00:00+00:00")
    daily.assert_awaited_once_with("@channel", "2026-02-02T12:00:00+00:00")


@pytest.mark.asyncio
async def test_zero_daily_cap_avoids_daily_storage_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hourly, daily = _patch_pool_reads(monkeypatch, daily_cap=0)

    pool = await engine._load_selection_pool(
        "campaign", "@channel", datetime(2026, 2, 3, 12, 0, tzinfo=UTC)
    )

    assert pool.daily_counts == {}
    daily.assert_not_awaited()


def test_health_rejects_low_trust_before_readiness_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trust = SimpleNamespace(score=49)
    score = Mock(return_value=trust)
    readiness = Mock()
    monkeypatch.setattr(engine, "account_trust_score_from", score)
    monkeypatch.setattr(engine, "evaluate_readiness", readiness)
    pool = engine._SelectionPool(
        accounts={"a": _account("a")},
        ready_account_ids=frozenset({"a"}),
        states={},
        spam={},
        fingerprints={},
        hourly_counts={},
        daily_counts={},
        limits=_limits(daily=0),
    )

    assert engine._is_healthy(_account("a"), 1, datetime.now(UTC), pool) is False
    readiness.assert_not_called()


def test_health_passes_cached_language_and_signals_to_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spam = SpamStatusVerdict(account_id="a", status="clean", checked_at="2026-01-01T00:00:00+00:00")
    state = WarmingStateRecord(
        account_id="a", state="active", updated_at="2026-01-01T00:00:00+00:00"
    )
    fingerprint = DeviceFingerprint(
        account_id="a",
        platform="linux",
        device_model="model",
        system_version="1",
        app_version="1",
        lang_code="ru",
        system_lang_code="ru",
    )
    trust = SimpleNamespace(score=50)
    score = Mock(return_value=trust)
    readiness = Mock(return_value=SimpleNamespace(ready=True))
    monkeypatch.setattr(engine, "account_trust_score_from", score)
    monkeypatch.setattr(engine, "evaluate_readiness", readiness)
    pool = engine._SelectionPool(
        accounts={"a": _account("a")},
        ready_account_ids=frozenset({"a"}),
        states={"a": state},
        spam={"a": spam},
        fingerprints={"a": fingerprint},
        hourly_counts={},
        daily_counts={},
        limits=_limits(daily=0),
    )
    now = datetime.now(UTC)

    assert engine._is_healthy(_account("a"), 2, now, pool) is True
    assert score.call_args.kwargs == {
        "account": _account("a"),
        "record": state,
        "spam": spam,
        "lang_code": "ru",
        "now": now,
    }
    readiness.assert_called_once_with(_account("a"), 2, spam=spam, trust_score=trust)
