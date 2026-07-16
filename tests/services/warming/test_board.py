"""Warming tests split from the former service test module: test_board.py."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    create_account,
    update_account_from_session_check,
    upsert_spam_status,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.trust import TrustScore
from schemas.warming import (
    StopWarmingRequest,
    WarmingStateWrite,
)
from services import warming
from tests.services.warming._support import (
    _seed_ready_account,
    _set_settings,
)


@pytest.mark.asyncio
async def test_load_board_splits_idle_and_warming() -> None:
    await create_account(AccountCreate(account_id="acc-idle"))
    await create_account(AccountCreate(account_id="acc-warming"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-warming", state="active"))

    board = await warming.load_board()

    assert {card.account_id for card in board.idle} == {"acc-idle"}
    assert {card.account_id for card in board.warming} == {"acc-warming"}
    assert board.active_count == 1


@pytest.mark.asyncio
async def test_load_board_enriches_cards_and_summary() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1",
            session_path="acc-1",
            status="alive",
            is_temporary=False,
        ),
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="clean",
            checked_at="2026-06-13T00:00:00+00:00",
        ),
    )

    board = await warming.load_board()

    card = next(c for c in (*board.idle, *board.warming) if c.account_id == "acc-1")
    assert card.trust_score is not None
    assert card.trust_band is not None
    assert card.spam_status == "clean"
    assert card.age_hours is not None
    assert board.summary.total == 1


@pytest.mark.asyncio
async def test_load_board_card_exposes_target_days() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            started_at="2026-06-01T00:00:00+00:00",
            target_days=10,
        ),
    )

    board = await warming.load_board()

    card = next(c for c in board.warming if c.account_id == "acc-1")
    assert card.target_days == 10


@pytest.mark.asyncio
async def test_stop_unknown_account_returns_idle_card() -> None:
    # No account row exists — _current_card falls back to the id as the label.
    stopped = await warming.stop_warming(StopWarmingRequest(account_id="ghost"))

    assert stopped.account_id == "ghost"
    assert stopped.label == "ghost"
    assert stopped.state == "idle"


@pytest.mark.asyncio
async def test_load_board_attaches_readiness() -> None:
    await create_account(AccountCreate(account_id="acc-1"))  # not ready

    board = await warming.load_board()

    card = board.idle[0]
    assert card.readiness is not None
    assert card.readiness.ready is False


@pytest.mark.asyncio
async def test_load_board_dm_chip_mirrors_engine_readiness_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The DM chip must match what the engine does: readiness gates DM only when
    # enforce_readiness is on. With it off the engine skips the readiness gate,
    # so a not-ready but DM-eligible account must still show DM allowed (review).
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    monkeypatch.setattr(
        "services.warming.board.account_trust_score_from",
        lambda **_: TrustScore(account_id="acc-1", score=90, band="good"),
    )
    await create_account(AccountCreate(account_id="acc-1"))  # no proxy/session/channels → not ready

    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    assert (await warming.load_board()).idle[0].dm_allowed is True

    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=True)
    assert (await warming.load_board()).idle[0].dm_allowed is False


@pytest.mark.asyncio
async def test_summary_ready_counts_only_startable_accounts() -> None:
    """«Готовы» must count startable (idle) accounts, not already-warming ones (#98)."""
    await _seed_ready_account("acc-idle")
    await _seed_ready_account("acc-warm")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-warm", state="active"))

    board = await warming.load_board()

    assert board.summary.warming == 1
    assert board.summary.ready == 1  # only acc-idle; acc-warm is already warming
