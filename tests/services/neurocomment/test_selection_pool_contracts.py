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
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], AsyncMock, AsyncMock, AsyncMock]:
    account_ids = ["a", "skipped", "banned", "other"]
    monkeypatch.setattr(
        engine,
        "list_accounts_by_ids",
        AsyncMock(return_value=AccountList(accounts=[_account("a")])),
    )
    readiness = AsyncMock(
        return_value=SimpleNamespace(
            readiness=[
                SimpleNamespace(
                    account_id="a",
                    ready=True,
                    human_skipped=False,
                    banned=False,
                ),
                SimpleNamespace(
                    account_id="skipped",
                    ready=True,
                    human_skipped=True,
                    banned=False,
                ),
                SimpleNamespace(
                    account_id="banned",
                    ready=True,
                    human_skipped=False,
                    banned=True,
                ),
            ]
        )
    )
    monkeypatch.setattr(
        engine,
        "list_channel_readiness",
        readiness,
    )
    monkeypatch.setattr(engine, "list_warming_states_by_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(engine, "list_spam_statuses_by_ids", AsyncMock(return_value={}))
    monkeypatch.setattr(engine, "list_device_fingerprints_by_ids", AsyncMock(return_value={}))
    hourly = AsyncMock(return_value=SimpleNamespace(counts=[]))
    daily = AsyncMock(return_value=SimpleNamespace(counts=[]))
    monkeypatch.setattr(engine, "count_comments_per_account_since", hourly)
    monkeypatch.setattr(engine, "count_channel_comments_per_account_since", daily)
    return account_ids, readiness, hourly, daily


@pytest.mark.asyncio
async def test_pool_excludes_skipped_banned_and_other_channel_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_ids, readiness, hourly, daily = _patch_pool_reads(monkeypatch)
    now = datetime(2026, 2, 3, 12, 0, tzinfo=UTC)

    pool = await engine._load_selection_pool(
        "campaign", "@channel", account_ids, now, _limits(daily=2)
    )

    assert pool.ready_account_ids == frozenset({"a"})
    readiness.assert_awaited_once_with("campaign", "@channel", account_ids)
    hourly.assert_awaited_once_with(account_ids, "2026-02-03T11:00:00+00:00")
    daily.assert_awaited_once_with("@channel", account_ids, "2026-02-02T12:00:00+00:00")


@pytest.mark.asyncio
async def test_zero_daily_cap_avoids_daily_storage_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_ids, _readiness, _hourly, daily = _patch_pool_reads(monkeypatch)

    pool = await engine._load_selection_pool(
        "campaign",
        "@channel",
        account_ids,
        datetime(2026, 2, 3, 12, 0, tzinfo=UTC),
        _limits(daily=0),
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
