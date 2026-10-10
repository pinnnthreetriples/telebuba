"""Fixtures for user-parser service tests: a fresh DB, a scripted Telegram, no real waits."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import update

from core.db import _accounts, _get_engine, configure_database, create_account
from schemas.accounts import AccountCreate
from services import _account_owner
from services.user_parser import _seams, _state
from tests.services.user_parser.fakes import NOW, FakeTelegram

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_user_parser(tmp_path: Path) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    yield
    _state.reset_for_tests()
    _account_owner.reset_for_tests()


class Seams:
    """What the run asked of the clock and the fleet: its pauses, the cooldowns it set."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.cooldowns: dict[str, datetime] = {}
        self.cooling: set[str] = set()


@pytest.fixture
def seams(monkeypatch: pytest.MonkeyPatch) -> Seams:
    recorded = Seams()

    async def _sleep(seconds: float) -> None:
        recorded.sleeps.append(seconds)
        await asyncio.sleep(0)

    async def _slot(*_args: object) -> None:
        return None

    async def _set_cooldown(account_id: str, until: datetime) -> None:
        recorded.cooldowns[account_id] = until

    monkeypatch.setattr(_seams, "now", lambda: NOW)
    monkeypatch.setattr(_seams, "sleep", _sleep)
    monkeypatch.setattr(_seams, "await_send_slot", _slot)
    monkeypatch.setattr(_seams, "set_cooldown", _set_cooldown)
    monkeypatch.setattr(_seams, "in_cooldown", lambda account_id: account_id in recorded.cooling)
    return recorded


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch, seams: Seams) -> FakeTelegram:  # noqa: ARG001 - order
    fake = FakeTelegram()
    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    return fake


async def seed_accounts(*account_ids: str, user_ids: dict[str, int] | None = None) -> None:
    """Accounts with a session, so they may read; ``user_ids`` marks them as ours."""
    for account_id in account_ids:
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )
    for account_id, user_id in (user_ids or {}).items():
        with _get_engine().begin() as connection:
            connection.execute(
                update(_accounts)
                .where(_accounts.c.account_id == account_id)
                .values(user_id=user_id)
            )
