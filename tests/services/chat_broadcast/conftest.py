"""Local fixtures for chat-broadcast service tests: a fresh DB, a scripted Telegram, a clock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.db import configure_database
from services import _account_owner
from services.chat_broadcast import _runtime, _seams, _state
from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_chat_broadcast(tmp_path: Path) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    _reset()
    yield
    _reset()


def _reset() -> None:
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    _runtime.reset_for_tests()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The engine's clock and its sleeps: a sleep moves the clock instead of waiting."""
    fake = Clock()

    async def _sleep(seconds: float) -> None:
        fake.advance(seconds)
        await asyncio.sleep(0)

    async def _slot(*_args: object) -> None:
        return None

    monkeypatch.setattr(_seams, "now", fake.now)
    monkeypatch.setattr(_seams, "sleep", _sleep)
    monkeypatch.setattr(_seams, "await_send_slot", _slot)
    return fake


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> FakeTelegram:
    fake = FakeTelegram(clock=clock)
    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    monkeypatch.setattr(_seams, "solve_challenge", fake.solve_challenge)
    return fake
