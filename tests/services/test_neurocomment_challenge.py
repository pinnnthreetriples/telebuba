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
    resolve_pending_outcome,
    save_warming_settings,
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


@pytest.mark.asyncio
async def test_no_challenge_when_wait_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "execute_read", _wait(None))

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "no_challenge"
    assert _challenge_rows() == []


@pytest.mark.asyncio
async def test_image_challenge_solved_via_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    # A photo challenge with a downloaded image → Gemini vision decides + we click.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(has_photo=True, image_b64="aW1n")))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    outcome = await challenge.solve_if_present("acc-1", "@chan", 99)

    assert outcome == "solved"
    assert len(gemini.calls) == 1
    # The captcha image is forwarded to Gemini as an inline image part.
    assert gemini.calls[0].image_b64 == "aW1n"
    assert isinstance(execute.calls[0], ClickButton)
    assert [r["outcome"] for r in _challenge_rows()] == ["pending"]


@pytest.mark.asyncio
async def test_retry_on_re_challenge_then_solved(monkeypatch: pytest.MonkeyPatch) -> None:
    # Answer 1 is wrong → the bot re-challenges → we retry with the fresh challenge
    # and the second answer passes (no more re-challenge). Solved after 2 attempts.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(), _msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert len(execute.calls) == 2  # two answers dispatched (retry)
    assert [r["outcome"] for r in _challenge_rows()] == ["pending"]


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    # The bot keeps re-challenging → we stop after challenge_max_attempts (2) and fail,
    # rather than clicking forever (each wrong click risks a kick).
    monkeypatch.setattr(settings.neurocomment, "challenge_max_attempts", 2)
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(), _msg(), _msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "failed"
    assert len(execute.calls) == 2  # exactly max_attempts dispatches, no more
    assert [r["outcome"] for r in _challenge_rows()] == ["failed"]


@pytest.mark.asyncio
async def test_openai_provider_used_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Operator selected ChatGPT + set the OpenAI key → the captcha decision goes to the
    # OpenAI seam, not Gemini.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        gemini_api_key=None,
        openai_api_key="sk-test",
        captcha_llm_provider="openai",
    )
    openai = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    gemini = _gemini(GeminiResult(status="error"))
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text_openai", openai)
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert len(openai.calls) == 1  # routed to OpenAI
    assert gemini.calls == []  # Gemini untouched
    assert openai.calls[0].model == settings.openai.model


@pytest.mark.asyncio
async def test_image_challenge_no_image_gives_up_without_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # has_photo but the download failed (image_b64 None) → give up, no Gemini call.
    gemini = _gemini(GeminiResult(status="ok", text=_decision_text()))
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(has_photo=True, image_b64=None)))
    monkeypatch.setattr(_seams, "generate_text", gemini)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert gemini.calls == []
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_image_challenge_bypasses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # A cached solved decision exists for the same text+labels, but the image path
    # must ignore the cache (the picture varies) and call Gemini vision fresh.
    message = _msg(has_photo=True, image_b64="aW1n")
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
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(_seams, "execute_read", _wait(message))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=True).execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert len(gemini.calls) == 1  # vision path ignored the cache


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


# --- H3: image decisions must never pollute the text cache ---


@pytest.mark.asyncio
async def test_image_challenge_row_hash_is_photo_namespaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A photo challenge's audit row is keyed under the photo-namespaced hash, distinct
    # from the plain text hash, so a same-text/labels TEXT challenge can never reuse it.
    message = _msg(has_photo=True, image_b64="aW1n")
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(_seams, "execute_read", _wait(message))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=True).execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"

    photo_hash = challenge._challenge_hash(message.text, message.button_labels, has_photo=True)
    plain_hash = challenge._challenge_hash(message.text, message.button_labels)
    assert [r["challenge_hash"] for r in _challenge_rows()] == [photo_hash]
    assert photo_hash != plain_hash


@pytest.mark.asyncio
async def test_image_outcome_not_reused_by_text_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Solve an image challenge and mark it solved (cache-eligible), then present a TEXT
    # challenge with the same text+labels: the photo namespace misses → fresh Gemini call.
    photo = _msg(has_photo=True, image_b64="aW1n")
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(_seams, "execute_read", _wait(photo))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=True).execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)
    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert await resolve_pending_outcome("acc-1", "@chan", "solved") is True

    text_msg = _msg()  # same text + labels, no photo
    monkeypatch.setattr(_seams, "execute_read", _wait(text_msg))
    assert await challenge.solve_if_present("acc-2", "@chan2", 42) == "solved"
    assert len(gemini.calls) == 2  # cache MISS → the text challenge asked Gemini afresh


# --- M4: gate on confidence (fresh decisions only) ---


@pytest.mark.asyncio
async def test_low_confidence_gives_up_without_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    # A fresh decision below the confidence floor is dropped (→ give_up), nothing dispatched.
    gemini = _gemini(
        GeminiResult(
            status="ok",
            text=_decision_text(action="click_button", button_index=0, confidence=0.5),
        ),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []  # never acted on a low-confidence guess
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_confidence_at_threshold_acts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exactly at the floor (0.7) is allowed to act (the gate is a strict <).
    monkeypatch.setattr(settings.neurocomment, "challenge_min_confidence", 0.7)
    gemini = _gemini(
        GeminiResult(
            status="ok",
            text=_decision_text(action="click_button", button_index=0, confidence=0.7),
        ),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert isinstance(execute.calls[0], ClickButton)


@pytest.mark.asyncio
async def test_cached_low_confidence_decision_still_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The confidence floor gates only FRESH LLM calls; a cached (already-vetted) solved
    # decision is reused even if its stored confidence is below the floor.
    message = _msg()
    decision = ChallengeDecision(
        action="click_button", button_index=0, confidence=0.3, reasoning="r"
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

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert gemini.calls == []  # cached decision reused, never re-vetted for confidence
    assert isinstance(execute.calls[0], ClickButton)


# --- C2: screen a send_text action with the comment outbound filter ---


@pytest.mark.asyncio
async def test_send_text_with_link_gives_up_and_does_not_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A send_text answer containing a link fails the outbound filter → give_up, no post.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="join t.me/foo")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []  # nothing posted
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_send_text_with_forbidden_word_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # A forbidden marketing word ("купить") in the answer trips the outbound filter.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="купить сейчас")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []


@pytest.mark.asyncio
async def test_send_text_over_word_cap_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # An answer longer than the comment word cap is refused (same guard as the comment path).
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 2)
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="one two three")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []


# --- C1: screen the button label the dispatch will click ---


@pytest.mark.asyncio
async def test_click_dangerous_button_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gemini picks a denylisted button ("Connect Wallet") → the solver refuses to click it.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(
        _seams, "execute_read", _wait(_msg(button_labels=["Connect Wallet", "Skip"]))
    )
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_click_url_button_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # A button whose label is a link is treated as dangerous even without a denylist hit.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(
        _seams, "execute_read", _wait(_msg(button_labels=["t.me/joinme", "cancel"]))
    )
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []


@pytest.mark.asyncio
async def test_click_benign_button_still_clicks(monkeypatch: pytest.MonkeyPatch) -> None:
    # A safe label passes the screen and is clicked normally.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(button_labels=["I am human", "cancel"])))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    click = execute.calls[0]
    assert isinstance(click, ClickButton)
    assert click.button_text == "I am human"
