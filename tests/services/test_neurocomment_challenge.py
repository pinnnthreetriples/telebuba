"""Tests for ``services.neurocomment.challenge`` — the solver (Ф2 #145 + #146).

Telegram wait, Gemini, action dispatch and randomness are patched at the
``_seams`` seam; audit rows are verified through the real DB. asyncio.sleep is
neutralised so the humanize pause does not slow the suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    _get_engine,
    configure_database,
    create_account,
    create_campaign,
    fetch_readiness,
    insert_challenge,
    link_channel_to_campaign,
    update_solver_enabled,
    upsert_readiness,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.challenge import BotChallengeMessage, ChallengeDecision, ChallengeInsert
from schemas.gemini import GeminiResult
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import (
    ActionResult,
    BotChallengeWaitResult,
    ClickButton,
    LinkedDiscussionGroupResult,
    PostComment,
    WaitForBotChallenge,
)
from services.neurocomment import _seams, challenge

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.gemini import GeminiRequest
    from schemas.telegram_actions import TelegramAction, TelegramReadAction


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
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


def _wait(message: BotChallengeMessage | None) -> object:
    async def execute_read(_account_id: str, action: TelegramReadAction) -> object:
        assert isinstance(action, WaitForBotChallenge)
        return BotChallengeWaitResult(message=message)

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


def _msg(*, has_photo: bool = False) -> BotChallengeMessage:
    return BotChallengeMessage(
        text="prove you are human", button_labels=["yes", "no"], message_id=7, has_photo=has_photo
    )


def _challenge_rows() -> list[dict[str, object]]:
    with _get_engine().connect() as connection:
        return [
            dict(row)
            for row in connection.exec_driver_sql(
                "SELECT outcome, decision_json FROM neurocomment_challenges",
            ).mappings()
        ]


@pytest.mark.asyncio
async def test_no_challenge_when_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", _wait(None))

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "no_challenge"
    assert _challenge_rows() == []


@pytest.mark.asyncio
async def test_image_challenge_gives_up_without_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini = _gemini(GeminiResult(status="ok", text=_decision_text()))
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(has_photo=True)))
    monkeypatch.setattr(_seams, "generate_text", gemini)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert gemini.calls == []
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_cache_hit_skips_gemini_and_clicks(monkeypatch: pytest.MonkeyPatch) -> None:
    message = _msg()
    # Seed a solved row with a click decision under the same hash. The stored index is
    # relative to the sorted-label order (["no", "yes"]), so index 0 selects "no".
    decision = ChallengeDecision(
        action="click_button", button_index=0, confidence=0.9, reasoning="r"
    )
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=challenge._challenge_hash(message.text, message.button_labels),
            account_id="other-acc",
            channel="@other",
            raw_text=message.text,
            button_labels=message.button_labels,
            outcome="solved",
            decision_json=decision.model_dump_json(),
        ),
    )
    gemini = _gemini(GeminiResult(status="error"))
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(message))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    outcome = await challenge.solve_if_present("acc-1", "@chan", 99)

    assert outcome == "solved"
    assert gemini.calls == []  # cross-account cache reuse
    assert isinstance(execute.calls[0], ClickButton)
    # Replayed by label (order-robust), not the raw positional index.
    assert execute.calls[0].button_text == "no"


@pytest.mark.asyncio
async def test_cache_hit_clicks_correct_label_on_reordered_buttons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cached solved decision selected the "no" label. Decisions are stored relative
    # to the sorted-label order (["no", "yes"] here), so "no" is canonical index 0.
    # Applied to a message reordered as ["no", "yes"] (positional index 0 is "no") vs
    # ["yes", "no"], the solver must click "no" by label, not replay a raw position.
    original = BotChallengeMessage(
        text="prove you are human", button_labels=["yes", "no"], message_id=7
    )
    decision = ChallengeDecision(
        action="click_button", button_index=0, confidence=0.9, reasoning="r"
    )
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=challenge._challenge_hash(original.text, original.button_labels),
            account_id="other-acc",
            channel="@other",
            raw_text=original.text,
            button_labels=original.button_labels,
            outcome="solved",
            decision_json=decision.model_dump_json(),
        ),
    )
    reordered = BotChallengeMessage(
        text="prove you are human", button_labels=["no", "yes"], message_id=8
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(reordered))
    monkeypatch.setattr(_seams, "generate_text", _gemini(GeminiResult(status="error")))
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    outcome = await challenge.solve_if_present("acc-1", "@chan", 99)

    assert outcome == "solved"
    click = execute.calls[0]
    assert isinstance(click, ClickButton)
    # "no" is index 0 in the reordered layout — not the cached positional index 1.
    assert click.button_text == "no"


@pytest.mark.asyncio
async def test_cache_miss_calls_gemini_and_records_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    outcome = await challenge.solve_if_present("acc-1", "@chan", 99)

    assert outcome == "solved"
    assert len(gemini.calls) == 1
    assert isinstance(execute.calls[0], ClickButton)
    rows = _challenge_rows()
    assert [r["outcome"] for r in rows] == ["pending"]
    assert rows[0]["decision_json"] is not None


@pytest.mark.asyncio
async def test_gemini_timeout_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _timeout(_request: GeminiRequest) -> GeminiResult:
        raise TimeoutError

    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", _timeout)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_missing_gemini_key_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # No API key → GeminiRequest build raises; the solver gives up, not crashes.
    monkeypatch.setattr(settings.gemini, "api_key", "")
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_gemini_error_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", _gemini(GeminiResult(status="error")))

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"


@pytest.mark.asyncio
async def test_gemini_unparseable_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(
        _seams, "generate_text", _gemini(GeminiResult(status="ok", text="not json"))
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"


@pytest.mark.asyncio
async def test_gemini_decides_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(
        _seams, "generate_text", _gemini(GeminiResult(status="ok", text=_decision_text()))
    )

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_click_error_records_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=False).execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "failed"
    assert [r["outcome"] for r in _challenge_rows()] == ["failed"]


@pytest.mark.asyncio
async def test_send_text_dispatches_post_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="42")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert isinstance(execute.calls[0], PostComment)
    assert execute.calls[0].text == "42"


@pytest.mark.asyncio
async def test_humanize_pause_clamped_to_range(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(challenge.asyncio, "sleep", _record_sleep)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=True).execute)
    # lognorm fraction 1.0 → clamps to the max end of the range.
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 5.0)

    await challenge.solve_if_present("acc-1", "@chan", 99)

    assert sleeps == [settings.neurocomment.challenge_click_delay_max_seconds]


def test_challenge_hash_is_stable_and_label_order_insensitive() -> None:
    first = challenge._challenge_hash("Press to stay", ["Yes", "No"])
    second = challenge._challenge_hash("press   to  stay", ["No", "Yes"])
    assert first == second


@pytest.mark.asyncio
async def test_retry_pair_clears_readiness_and_reruns_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ф2 #148: retry erases the old (not-ready) readiness and re-onboards, re-running
    # the solver. With the solver enabled and no challenge this time → ready.
    await create_account(AccountCreate(account_id="acc-1"))
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await update_solver_enabled(campaign.campaign_id, value=True)
    await upsert_readiness("acc-1", "@chan", joined=True, captcha_passed=False, ready=False)

    waited: list[object] = []

    async def execute_read(_account_id: str, action: object) -> object:
        if isinstance(action, WaitForBotChallenge):
            waited.append(action)
            return BotChallengeWaitResult(message=None)
        return LinkedDiscussionGroupResult(linked_chat_id=77, comments_enabled=True)

    async def execute(account_id: str, action: object) -> ActionResult:
        action_type = str(getattr(action, "action_type", "join_discussion_group"))
        return ActionResult(status="ok", action_type=action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute_read", execute_read)
    monkeypatch.setattr(_seams, "execute", execute)

    outcome = await challenge.retry_pair("acc-1", "@chan")

    assert waited  # the solver's WaitForBotChallenge ran during the re-onboard
    assert outcome.state == "ready"
    readiness = await fetch_readiness("acc-1", "@chan")
    assert readiness is not None
    assert readiness.ready is True
