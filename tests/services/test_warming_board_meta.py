"""The ready/warming card surfaces the account's real proxy type + phone.

Replaces the warming page's design-first ``PROXY_TYPES[hash]`` mock: the card
now reads the assigned pool proxy's type (or ``None`` when unassigned).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, create_account
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from services.warming import load_board
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
