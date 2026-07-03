"""The ready/warming card surfaces the account's real proxy type + phone.

Replaces the warming page's design-first ``PROXY_TYPES[hash]`` mock: the card
now reads the assigned pool proxy's type (or ``None`` when unassigned).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, create_account
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.warming import WarmingStateRecord
from services.warming import load_board
from services.warming.board import _warming_days_since
from tests.factories import seed_account_proxy

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


@pytest.mark.asyncio
async def test_board_card_carries_assigned_proxy_type() -> None:
    await create_account(AccountCreate(account_id="with-proxy", label="Has Proxy"))
    await create_account(AccountCreate(account_id="no-proxy", label="No Proxy"))
    await seed_account_proxy("with-proxy")

    board = await load_board()
    cards = {card.account_id: card for card in board.idle}

    assert cards["with-proxy"].proxy_type == "socks5"
    assert cards["no-proxy"].proxy_type is None
    # phone mirrors the account record (None until a session check populates it).
    assert cards["with-proxy"].phone is None


def test_warming_days_frozen_after_stop() -> None:
    """A stopped record's card day-count is capped at ``stopped_at`` (not now)."""
    started = datetime(2026, 1, 1, tzinfo=UTC)
    stopped = started + timedelta(days=3)
    now = started + timedelta(days=40)  # 37 days of wall-clock after the stop
    record = WarmingStateRecord(
        account_id="stopped",
        state="idle",  # non-warming → the count must freeze at stopped_at
        updated_at=stopped.isoformat(),
        started_at=started.isoformat(),
        stopped_at=stopped.isoformat(),
    )

    assert _warming_days_since(record, now) == 3


@pytest.mark.asyncio
async def test_board_serves_card_log_limit_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The board payload carries ``card_log_limit`` from config (dead tunable revived)."""
    monkeypatch.setattr(settings.warming, "card_log_limit", 42)
    await create_account(AccountCreate(account_id="acc", label="Acc"))

    board = await load_board()

    assert board.card_log_limit == 42


@pytest.mark.asyncio
async def test_board_card_carries_phase_enum_not_label() -> None:
    """The card exposes the ``phase`` enum (SPA translates it), no pre-translated label."""
    await create_account(AccountCreate(account_id="acc", label="Acc"))

    board = await load_board()
    card = board.idle[0]

    assert card.phase is not None  # e.g. "intro" — the locale-neutral enum
    # The Russian ``phase_label`` field has been removed from the wire (#12).
    assert not hasattr(card, "phase_label")
