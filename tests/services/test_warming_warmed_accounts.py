"""Tests for ``services.warming.list_warmed_accounts`` (neurocomment overview seam)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    create_account,
    mark_promoted_to_nc,
    upsert_warming_state,
)
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
    # Promotion is the gate now — even an over-threshold account stays out until
    # the operator graduates it. ``young`` is also promoted to prove the day
    # floor still excludes it.
    await mark_promoted_to_nc("old")
    await mark_promoted_to_nc("young")

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
    await mark_promoted_to_nc("a")
    await mark_promoted_to_nc("b")

    result = await list_warmed_accounts(14)

    # Most-warmed first.
    assert [a.account_id for a in result.accounts] == ["a", "b"]


@pytest.mark.asyncio
async def test_list_warmed_accounts_empty_when_none_warmed() -> None:
    await create_account(AccountCreate(account_id="fresh", label="Fresh"))
    result = await list_warmed_accounts(14)
    assert result.accounts == []


@pytest.mark.asyncio
async def test_list_warmed_accounts_skips_unpromoted_even_when_old_enough() -> None:
    """Crossing ``min_days`` alone no longer auto-graduates: the operator must promote."""
    await create_account(AccountCreate(account_id="patient", label="Patient"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="patient", state="active", started_at=_days_ago(40)),
    )

    result = await list_warmed_accounts(14)

    assert result.accounts == []


@pytest.mark.asyncio
async def test_promote_to_neurocomment_appears_in_warmed_list_after_threshold() -> None:
    """The card button stops warming and flips the flag; the warmed-list picks it up."""
    from services.warming import promote_to_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="graduate", label="Graduate"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="graduate", state="active", started_at=_days_ago(30)),
    )

    card = await promote_to_neurocomment("graduate")
    assert card.promoted_to_nc is True
    assert card.state == "idle"  # warming loop stopped

    result = await list_warmed_accounts(14)
    assert [a.account_id for a in result.accounts] == ["graduate"]


@pytest.mark.asyncio
async def test_warmed_account_carries_card_meta() -> None:
    """The warmed entry surfaces the card's proxy type + target days (de-mock enrichment)."""
    from services.warming import promote_to_neurocomment  # noqa: PLC0415
    from tests.factories import seed_account_proxy  # noqa: PLC0415

    await create_account(AccountCreate(account_id="meta", label="Meta"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="meta", state="active", started_at=_days_ago(20)),
    )
    await seed_account_proxy("meta")
    await promote_to_neurocomment("meta")

    result = await list_warmed_accounts(14)

    assert len(result.accounts) == 1
    warmed = result.accounts[0]
    assert warmed.proxy_type == "socks5"
    assert warmed.target_days == 14


@pytest.mark.asyncio
async def test_unmark_neurocomment_removes_from_warmed_list() -> None:
    """The un-promote affordance flips the flag back; the warmed-list drops the account."""
    from services.warming import promote_to_neurocomment, unmark_neurocomment  # noqa: PLC0415

    await create_account(AccountCreate(account_id="returner", label="Returner"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="returner", state="active", started_at=_days_ago(30)),
    )
    await promote_to_neurocomment("returner")
    assert [a.account_id for a in (await list_warmed_accounts(14)).accounts] == ["returner"]

    card = await unmark_neurocomment("returner")

    assert card.promoted_to_nc is False
    assert (await list_warmed_accounts(14)).accounts == []


@pytest.mark.asyncio
async def test_start_warming_clears_promotion_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dragging a graduated card back into warming must drop it from the NC pool (Bug 2)."""
    import asyncio  # noqa: PLC0415

    from core.db import save_warming_settings  # noqa: PLC0415
    from schemas.warming import StartWarmingRequest, StopWarmingRequest  # noqa: PLC0415
    from services.warming import (  # noqa: PLC0415
        _RUNTIME,
        _runtime,
        promote_to_neurocomment,
        start_warming,
        stop_warming,
    )

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)

    await create_account(AccountCreate(account_id="boomerang", label="Boomerang"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="boomerang", state="active", started_at=_days_ago(30)),
    )
    # Disable readiness gate so start_warming proceeds without proxy/spam setup.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )

    await promote_to_neurocomment("boomerang")
    assert [a.account_id for a in (await list_warmed_accounts(14)).accounts] == ["boomerang"]

    card = await start_warming(StartWarmingRequest(account_id="boomerang"))

    assert card.promoted_to_nc is False
    assert (await list_warmed_accounts(14)).accounts == []

    # Cleanup: cancel the background loop task so the test doesn't leak.
    await stop_warming(StopWarmingRequest(account_id="boomerang"))
    assert "boomerang" not in _RUNTIME
