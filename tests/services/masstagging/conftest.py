"""Local fixtures for masstagging service tests: a fresh DB, a scripted Telegram, a clock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database
from services import _account_owner
from services.masstagging import _runtime, _seams, _state
from tests.services.masstagging.fakes import Clock, FakeTelegram

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_masstagging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.masstagging, "media_dir", tmp_path / "media")
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

    monkeypatch.setattr(_seams, "now", fake.now)
    monkeypatch.setattr(_seams, "sleep", _sleep)
    return fake


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> FakeTelegram:
    fake = FakeTelegram(clock=clock)
    monkeypatch.setattr(_seams, "execute", fake.execute)
    monkeypatch.setattr(_seams, "execute_read", fake.execute_read)
    return fake
