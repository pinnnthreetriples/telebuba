"""Tests for ``services.neurocomment.challenge`` — detection-only solver (#145).

The Telegram wait is patched at the ``_seams.execute_read`` seam; the audit row is
verified through the real repository. No Gemini in this slice — every detected
challenge is ``give_up``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, list_failed_for_channel
from core.logging import reset_logging_for_tests, setup_logging
from schemas.challenge import BotChallengeMessage
from schemas.telegram_actions import BotChallengeWaitResult, WaitForBotChallenge
from services.neurocomment import _seams, challenge

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.telegram_actions import TelegramReadAction


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _wait_returning(message: BotChallengeMessage | None) -> object:
    async def execute_read(_account_id: str, action: TelegramReadAction) -> object:
        assert isinstance(action, WaitForBotChallenge)
        return BotChallengeWaitResult(message=message)

    return execute_read


@pytest.mark.asyncio
async def test_no_challenge_when_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", _wait_returning(None))

    outcome = await challenge.solve_if_present("acc-1", "@chan", 999)

    assert outcome == "no_challenge"
    assert (await list_failed_for_channel("@chan", limit=10)).rows == []


@pytest.mark.asyncio
async def test_detected_challenge_records_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    message = BotChallengeMessage(
        text="2+2=?", button_labels=["4", "5"], message_id=10, has_photo=False
    )
    monkeypatch.setattr(_seams, "execute_read", _wait_returning(message))

    outcome = await challenge.solve_if_present("acc-1", "@chan", 999)

    assert outcome == "give_up"
    rows = (await list_failed_for_channel("@chan", limit=10)).rows
    assert len(rows) == 1
    assert rows[0].outcome == "give_up"
    assert rows[0].raw_text == "2+2=?"
    assert rows[0].button_labels == ["4", "5"]


@pytest.mark.asyncio
async def test_image_challenge_also_gives_up_without_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Phase 1: an image challenge is still give_up (vision deferred to #146+).
    message = BotChallengeMessage(text="", button_labels=["ok"], message_id=11, has_photo=True)
    monkeypatch.setattr(_seams, "execute_read", _wait_returning(message))

    assert await challenge.solve_if_present("acc-1", "@chan", 999) == "give_up"


def test_challenge_hash_is_stable_and_label_order_insensitive() -> None:
    # Same normalized text + same label set (any order) → same cache key.
    first = challenge._challenge_hash("Press to stay", ["Yes", "No"])
    second = challenge._challenge_hash("press   to  stay", ["No", "Yes"])
    assert first == second
