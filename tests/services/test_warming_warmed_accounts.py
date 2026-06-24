"""Tests for ``services.warming.list_warmed_accounts`` (neurocomment overview seam)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, create_account, upsert_warming_state
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateWrite
from services.warming import list_warmed_accounts

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _days_ago(days: int) -> str:
    # +1 h so the whole-day floor lands exactly on ``days``.
    return (datetime.now(UTC) - timedelta(days=days, hours=1)).isoformat()


@pytest.mark.asyncio
async def test_list_warmed_accounts_keeps_only_threshold_and_above() -> None:
    await create_account(AccountCreate(account_id="old", label="Old"))
    await create_account(AccountCreate(account_id="young", label="Young"))
    await create_account(AccountCreate(account_id="fresh", label="Fresh"))  # never warmed
    await upsert_warming_state(
        WarmingStateWrite(account_id="old", state="active", started_at=_days_ago(20)),
    )
    await upsert_warming_state(
        WarmingStateWrite(account_id="young", state="active", started_at=_days_ago(5)),
    )

    result = await list_warmed_accounts(14)

    assert [a.account_id for a in result.accounts] == ["old"]
    assert result.accounts[0].label == "Old"
    assert result.accounts[0].warming_days >= 14


@pytest.mark.asyncio
async def test_list_warmed_accounts_sorted_newest_warmed_first() -> None:
    await create_account(AccountCreate(account_id="a", label="A"))
    await create_account(AccountCreate(account_id="b", label="B"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="a", state="active", started_at=_days_ago(30)),
    )
    await upsert_warming_state(
        WarmingStateWrite(account_id="b", state="active", started_at=_days_ago(15)),
    )

    result = await list_warmed_accounts(14)

    # Most-warmed first.
    assert [a.account_id for a in result.accounts] == ["a", "b"]


@pytest.mark.asyncio
async def test_list_warmed_accounts_empty_when_none_warmed() -> None:
    await create_account(AccountCreate(account_id="fresh", label="Fresh"))
    result = await list_warmed_accounts(14)
    assert result.accounts == []
