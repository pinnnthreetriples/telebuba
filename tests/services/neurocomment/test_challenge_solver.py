"""Tests for neurocomment challenge solver behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    create_account,
    create_campaign,
    fetch_readiness,
    insert_challenge,
    link_channel_to_campaign,
    save_warming_settings,
    update_solver_enabled,
    upsert_readiness,
)
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
    from schemas.gemini import GeminiRequest


from tests.services.neurocomment.challenge_support import (
    _challenge_rows,
    _decision_text,
    _ExecuteStub,
    _gemini,
    _msg,
    _wait,
)

pytestmark = pytest.mark.usefixtures("isolate_challenge")


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
