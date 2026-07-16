"""Shared fixtures and stubs for neurocomment challenge tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    _get_engine,
    configure_database,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.challenge import BotChallengeMessage, ChallengeDecision
from schemas.telegram_actions import (
    ActionResult,
    BotChallengeWaitResult,
    WaitForBotChallenge,
)
from services.neurocomment import challenge

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.gemini import GeminiRequest, GeminiResult
    from schemas.telegram_actions import TelegramAction, TelegramReadAction


@pytest.fixture
def isolate_challenge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # GeminiRequest requires a non-empty key; CI has none, so set one explicitly.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    reset_logging_for_tests()
    setup_logging()
    # Neutralise the humanize pause.
    monkeypatch.setattr(challenge.asyncio, "sleep", _no_sleep)
    yield
    reset_logging_for_tests()


async def _no_sleep(_seconds: float) -> None:
    return None


class _ExecuteStub:
    """Records dispatched write-actions; returns a configurable ActionResult."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[TelegramAction] = []

    async def execute(self, _account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append(action)
        return ActionResult(
            status="ok" if self.ok else "failed",
            action_type=action.action_type,
            account_id="x",
            error_type=None if self.ok else "ChatWriteForbiddenError",
        )


def _wait(*messages: BotChallengeMessage | None) -> object:
    """Return each queued message on successive WaitForBotChallenge calls, then None.

    The solver waits once for the initial challenge and again (a "re-check") after
    each answer; an exhausted queue → None models "no re-challenge = answer passed".
    """
    queue = list(messages)

    async def execute_read(_account_id: str, action: TelegramReadAction) -> object:
        assert isinstance(action, WaitForBotChallenge)
        return BotChallengeWaitResult(message=queue.pop(0) if queue else None)

    return execute_read


class _GeminiStub:
    """Callable Gemini seam stub that records requests and returns a canned result."""

    def __init__(self, result: GeminiResult) -> None:
        self._result = result
        self.calls: list[GeminiRequest] = []

    async def __call__(self, request: GeminiRequest) -> GeminiResult:
        self.calls.append(request)
        return self._result


def _gemini(result: GeminiResult) -> _GeminiStub:
    return _GeminiStub(result)


def _decision_text(**kw: object) -> str:
    base: dict[str, object] = {"action": "give_up", "confidence": 0.9, "reasoning": "r"}
    base.update(kw)
    return ChallengeDecision(**base).model_dump_json()  # ty: ignore[invalid-argument-type]


def _msg(
    *,
    has_photo: bool = False,
    image_b64: str | None = None,
    button_labels: list[str] | None = None,
    text: str = "prove you are human",
) -> BotChallengeMessage:
    return BotChallengeMessage(
        text=text,
        button_labels=["yes", "no"] if button_labels is None else button_labels,
        message_id=7,
        has_photo=has_photo,
        image_b64=image_b64,
    )


def _challenge_rows() -> list[dict[str, object]]:
    with _get_engine().connect() as connection:
        return [
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT outcome, decision_json, challenge_hash FROM neurocomment_challenges",
            ).mappings()
        ]
