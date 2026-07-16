"""Shared fixtures and stubs for neurocomment engine tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    configure_database,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import ActionResult
from services.neurocomment import _generate, _seams, _state, engine

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from pathlib import Path

    from schemas.telegram_actions import ActionStatus, TelegramAction


@pytest.fixture
def isolate_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # GeminiRequest requires a non-empty key; a real deployment sets GEMINI__API_KEY.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    reset_logging_for_tests()
    setup_logging()
    _state.reset_for_tests()
    # asyncio.Lock binds to the running loop and the in-flight map is process-global,
    # so both must be cleared per test (a stale lock from another loop would deadlock).
    engine._ACCOUNT_LOCKS.clear()
    _generate._INFLIGHT.clear()
    # Generation/post never actually wait.
    monkeypatch.setattr(engine.asyncio, "sleep", _no_sleep)
    # Default health: the readiness gate is forced open. Trust is scored from bulk
    # signals via the pure account_trust_score_from and ignored here (evaluate_readiness
    # is stubbed); spam comes from the cached bulk read, never a live probe.
    monkeypatch.setattr(engine, "evaluate_readiness", lambda *_a, **_k: _Readiness(ready=True))
    yield
    _state.reset_for_tests()


class _Readiness:
    def __init__(self, *, ready: bool, reasons: list[str] | None = None) -> None:
        self.ready = ready
        self.reasons = reasons or []


async def _no_sleep(_seconds: float) -> None:
    return None


def _async_return(value: object) -> Callable[..., Awaitable[object]]:
    async def _fn(*_a: object, **_k: object) -> object:
        return value

    return _fn


class _CommentStub:
    """Captures ``CommentOnPost`` calls and returns a canned ``ActionResult``."""

    def __init__(
        self,
        *,
        status: ActionStatus = "ok",
        message_id: int | None = 555,
        error_type: str | None = None,
        flood_wait_seconds: int | None = None,
    ) -> None:
        self.status = status
        self.message_id = message_id
        self.error_type = error_type
        self.flood_wait_seconds = flood_wait_seconds
        self.calls: list[tuple[str, TelegramAction]] = []

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.calls.append((account_id, action))
        return ActionResult(
            status=self.status,
            action_type=action.action_type,
            account_id=account_id,
            message_id=self.message_id if self.status == "ok" else None,
            error_type=self.error_type,
            flood_wait_seconds=self.flood_wait_seconds,
        )


class _GenStub:
    """Returns a sequence of canned generated texts (cycles the last one)."""

    def __init__(self, *texts: str) -> None:
        self.texts = list(texts)
        self.calls = 0

    async def generate_text(self, _request: object) -> GeminiResult:
        text = self.texts[min(self.calls, len(self.texts) - 1)]
        self.calls += 1
        return GeminiResult(status="ok", text=text)


async def _make_campaign(channel: str, *accounts: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="Promo", prompt="mention X"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    for acc in accounts:
        await create_account(AccountCreate(account_id=acc, label=acc, session_name=acc))
        await assign_account_to_campaign(campaign.campaign_id, acc)
        await upsert_readiness(acc, channel, joined=True, captcha_passed=True, ready=True)
    return campaign.campaign_id


def _patch_io(
    monkeypatch: pytest.MonkeyPatch,
    *,
    comment: _CommentStub,
    gen: _GenStub | None = None,
) -> None:
    monkeypatch.setattr(_seams, "execute", comment.execute)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", (gen or _GenStub("a nice comment")).generate_text)


class _FixedRng:
    """Deterministic rng: ``choice`` picks the first item, ``uniform`` the low bound."""

    @staticmethod
    def choice(seq: list[str]) -> str:
        return seq[0]

    @staticmethod
    def uniform(low: float, _high: float) -> float:
        return low
