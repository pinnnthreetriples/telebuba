"""The AI rewrite and "AI for the chat": what it may change, and the fallback."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories.logs import list_recent_logs
from schemas.gemini import GeminiRequest, GeminiResult
from services._text_llm import TextLlm
from services.chat_broadcast import _seams, _state
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import FakeTelegram

_LLM = TextLlm(
    use_deepseek=False,
    api_key="key",
    model="m",
    temperature=0.5,
    max_output_tokens=256,
    max_retries=0,
    min_interval_seconds=0.0,
)


@dataclass
class Llm:
    answers: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> Llm:
    """The provider answers from a queue; every prompt it was asked is recorded."""
    fake = Llm()

    async def _generate(request: GeminiRequest) -> GeminiResult:
        fake.prompts.append(request.prompt)
        if not fake.answers:
            return GeminiResult(status="error", error="down")
        return GeminiResult(status="ok", text=fake.answers.pop(0))

    monkeypatch.setattr("services.chat_broadcast._rewrite.text_llm", lambda _secret: _LLM)
    monkeypatch.setattr(_seams, "generate_text", _generate)
    monkeypatch.setattr(_seams, "generate_text_deepseek", _generate)
    monkeypatch.setattr(settings.warming, "content_forbidden_words", ["casino"])
    return fake


@pytest.mark.asyncio
async def test_randomize_rewrites_each_send(telegram: FakeTelegram, llm: Llm) -> None:
    llm.answers.extend(["«Building bots — DM me @boss»"])
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha",), messages=("We make bots, DM @boss",), randomize=True
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Building bots — DM me @boss"]
    assert "We make bots, DM @boss" in llm.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        "Building bots, DM me",  # dropped the mention
        "Bots! Visit https://spam.example",  # invented a link
        "We do casino bots @boss",  # a forbidden word
        "x" * 500,  # wandered off
    ],
)
async def test_a_rewrite_that_breaks_the_rules_sends_the_original(
    telegram: FakeTelegram, llm: Llm, answer: str
) -> None:
    llm.answers.append(answer)
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha",), messages=("We make bots, DM @boss",), randomize=True
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["We make bots, DM @boss"]


@pytest.mark.asyncio
async def test_without_a_key_the_original_goes_and_the_fallback_is_logged_once(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyless = replace(_LLM, api_key="")
    monkeypatch.setattr("services.chat_broadcast._rewrite.text_llm", lambda _secret: keyless)
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha", "@beta"), messages=("Hi",), randomize=True
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Hi", "Hi"]
    logs = await list_recent_logs(limit=200)
    fallbacks = [row for row in logs if row.event == "chat_broadcast_rewrite_fallback"]
    assert len(fallbacks) == 1


@pytest.mark.asyncio
async def test_the_daily_budget_stops_rewrites(telegram: FakeTelegram, llm: Llm) -> None:
    llm.answers.extend(["Hey there", "Hello there"])
    _state.record_llm_call(calls=settings.chat_broadcast.max_llm_calls_per_day)
    campaign_id = await seed(
        accounts=("a1",), targets=("@alpha",), messages=("Hi there",), randomize=True
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Hi there"]
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_ai_writes_the_first_message_for_the_chat_only(
    telegram: FakeTelegram, llm: Llm
) -> None:
    llm.answers.extend(["Hi crypto folks, we build bots"])
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("fallback {group_title}", "Second"),
        first_message="ai",
        ai_brief="We build bots; friendly tone",
        randomize=True,
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Hi crypto folks, we build bots", "Second"]
    assert "We build bots" in llm.prompts[0]
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_ai_first_message_falls_back_to_the_template(
    telegram: FakeTelegram, llm: Llm
) -> None:
    del llm
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi {group_username}",),
        first_message="ai",
        ai_brief="anything",
    )

    await run_to_end(campaign_id)

    assert telegram.texts() == ["Hi @alpha"]
