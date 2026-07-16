"""Shared fixtures and stubs for neurocomment onboarding tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.gemini import GeminiResult
from schemas.spam_status import SpamStatusVerdict
from schemas.telegram_actions import (
    ActionResult,
    BotChallengeWaitResult,
    LinkedDiscussionGroupResult,
    WaitForBotChallenge,
)
from services.neurocomment import _seams, _state

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.challenge import BotChallengeMessage
    from schemas.telegram_actions import ActionStatus, TelegramAction, TelegramReadAction


@pytest.fixture
def isolate_onboarding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # GeminiRequest requires a non-empty key (the solver builds one); CI has none.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    reset_logging_for_tests()
    setup_logging()
    # onboard_campaign probes each account's spam once; keep it off the network.
    monkeypatch.setattr(_seams, "refresh_spam_status", _clean_spam)
    # The solver calls Gemini on a detected (non-image) challenge — keep it off the
    # network; an error verdict makes the solver give up (→ bot_challenge).
    monkeypatch.setattr(_seams, "generate_text", _gemini_error)
    # The solver is opt-in (#148, default off); enable it for the tests that assert
    # solver behaviour — the gating tests override this per case.
    monkeypatch.setattr(settings.neurocomment, "challenge_solver_enabled", True)
    _state.reset_for_tests()
    yield
    _state.reset_for_tests()
    reset_logging_for_tests()


async def _gemini_error(_request: object) -> GeminiResult:
    return GeminiResult(status="error", error="offline in tests")


class _ReadStub:
    """Canned reads: a linked-group result for resolve, a wait result for the solver."""

    def __init__(
        self,
        *,
        linked_chat_id: int | None,
        comments_enabled: bool,
        challenge: BotChallengeMessage | None = None,
    ) -> None:
        self.result = LinkedDiscussionGroupResult(
            linked_chat_id=linked_chat_id,
            comments_enabled=comments_enabled,
        )
        self.challenge = challenge
        self.calls: list[tuple[str, TelegramReadAction]] = []

    async def execute_read(self, account_id: str, action: TelegramReadAction) -> object:
        self.calls.append((account_id, action))
        if isinstance(action, WaitForBotChallenge):
            return BotChallengeWaitResult(message=self.challenge)
        return self.result


class _JoinStub:
    """Returns a canned join ``ActionResult`` keyed by channel, default ok."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, TelegramAction]] = []
        self.by_channel: dict[str, ActionResult] = {}

    def set(
        self,
        channel: str,
        *,
        status: ActionStatus,
        error_type: str | None = None,
        flood_wait_seconds: int | None = None,
    ) -> None:
        self.by_channel[channel] = ActionResult(
            status=status,
            action_type="join_discussion_group",
            account_id="x",
            error_type=error_type,
            flood_wait_seconds=flood_wait_seconds,
        )

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append((account_id, action))
        channel = getattr(action, "channel", "")
        if channel in self.by_channel:
            return self.by_channel[channel]
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
        )


def _no_sleep(records: list[float]) -> object:
    async def _sleep(seconds: float) -> None:
        records.append(seconds)

    return _sleep


async def _clean_spam(account_id: str, **_kwargs: object) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id, status="clean", checked_at="2026-01-01T00:00:00"
    )
