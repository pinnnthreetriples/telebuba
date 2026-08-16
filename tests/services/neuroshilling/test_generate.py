"""Generation: the DeepSeek call, the retry that feeds the complaint back, the repair.

The gateway is patched at ``services.neuroshilling._seams`` — never at
``core.openai`` — because that is the one seam the whole domain reaches it
through.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from schemas.gemini import GeminiResult
from services.neuroshilling import _generate, _seams, _state
from services.neuroshilling._prompt import DialogueAsk

if TYPE_CHECKING:
    from schemas.gemini import GeminiRequest


@pytest.fixture(autouse=True)
def _deployment_has_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite blanks the key for every test; this module is about the path that uses it."""
    monkeypatch.setattr(settings.deepseek, "api_key", "sk-deepseek")


class _Gateway:
    """Answers each attempt from a queue and records what it was asked."""

    def __init__(self, *answers: GeminiResult) -> None:
        self.answers = list(answers)
        self.requests: list[GeminiRequest] = []

    async def __call__(self, request: GeminiRequest) -> GeminiResult:
        self.requests.append(request)
        return self.answers.pop(0) if self.answers else GeminiResult(status="error")


def _answer(**overrides: Any) -> GeminiResult:
    body: dict[str, Any] = {
        "roles": [
            {"name": "Skeptic", "description": "doubts"},
            {"name": "Regular", "description": "calm"},
        ],
        "steps": [
            {"speaker_id": 1, "text": "anyone tried it?", "reply_to_index": None, "reaction": None},
            {"speaker_id": 2, "text": "a year now", "reply_to_index": 0, "reaction": None},
        ],
        **overrides,
    }
    return GeminiResult(status="ok", text=json.dumps(body, ensure_ascii=False))


async def _generate_with(
    monkeypatch: pytest.MonkeyPatch,
    gateway: _Gateway,
    **overrides: Any,
) -> Any:
    monkeypatch.setattr(_seams, "generate_text_deepseek", gateway)
    fields: dict[str, Any] = {
        "persona_count": 2,
        "step_count": 2,
        "unique_messages": True,
        **overrides,
    }
    role_ids = fields.pop("role_ids", ())
    return await _generate.generate_dialogue(
        "delivery",
        DialogueAsk(**fields),
        role_ids=role_ids,
    )


@pytest.mark.asyncio
async def test_a_json_object_answer_is_parsed_into_roles_and_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway(_answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert [role.name for role in draft.roles] == ["Skeptic", "Regular"]
    assert [step.text for step in draft.steps] == ["anyone tried it?", "a year now"]
    # The model counts indices; the domain counts positions.
    assert draft.steps[1].reply_to_position == 1
    # No stored roles yet, so the keys are the model's own speaker numbers.
    assert [step.role_id for step in draft.steps] == ["1", "2"]


@pytest.mark.asyncio
async def test_the_stored_role_ids_are_reused_positionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key naming nothing mints a new role and nulls the roster's foreign key."""
    draft = await _generate_with(monkeypatch, _Gateway(_answer()), role_ids=("r-1", "r-2"))

    assert draft is not None
    assert [role.role_id for role in draft.roles] == ["r-1", "r-2"]
    assert [step.role_id for step in draft.steps] == ["r-1", "r-2"]


@pytest.mark.asyncio
async def test_a_persona_the_campaign_has_no_role_for_gets_a_fresh_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asking for more personas than are stored is normal; only the surplus is new."""
    draft = await _generate_with(monkeypatch, _Gateway(_answer()), role_ids=("r-1",))

    assert draft is not None
    assert [role.role_id for role in draft.roles] == ["r-1", "2"]


@pytest.mark.asyncio
async def test_the_request_asks_for_the_only_json_mode_deepseek_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _Gateway(_answer())

    await _generate_with(monkeypatch, gateway)

    request = gateway.requests[0]
    assert request.response_json_object is True
    assert request.response_schema_json is None
    # 0 is what ``core.openai`` renders as ``thinking: {"type": "disabled"}``:
    # DeepSeek bills reasoning to ``max_tokens``, so leaving it on spends the
    # dialogue's budget on thoughts no JSON caller reads.
    assert request.thinking_budget == 0
    assert "json" in request.prompt


@pytest.mark.asyncio
async def test_a_fenced_answer_is_unwrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fenced = GeminiResult(status="ok", text=f"```json\n{_answer().text}\n```")

    draft = await _generate_with(monkeypatch, _Gateway(fenced))

    assert draft is not None
    assert len(draft.steps) == 2


@pytest.mark.asyncio
async def test_a_validation_error_is_fed_back_into_the_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = GeminiResult(status="ok", text='{"roles": "not a list", "steps": []}')
    gateway = _Gateway(broken, _answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert len(gateway.requests) == 2
    assert "Your previous answer was rejected" in gateway.requests[1].prompt
    assert "roles" in gateway.requests[1].prompt.rsplit("rejected:", 1)[1]


@pytest.mark.asyncio
async def test_an_empty_body_is_retried_rather_than_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek documents that JSON mode may occasionally return empty content."""
    gateway = _Gateway(GeminiResult(status="ok", text=None), _answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_a_wrong_persona_count_is_re_asked_with_the_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lonely = _answer(roles=[{"name": "Only", "description": ""}])
    gateway = _Gateway(lonely, _answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert "exactly 2 entries" in gateway.requests[1].prompt


@pytest.mark.asyncio
async def test_an_exhausted_retry_budget_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neuroshilling, "llm_max_attempts", 2)
    gateway = _Gateway(GeminiResult(status="error"), GeminiResult(status="rate_limited"))

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is None
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_a_provider_error_string_is_never_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """``GeminiResult.error`` is ``f"{type(exc).__name__}: {exc}"`` — proxy URLs and all."""
    leak = "ProxyError: socks5://user:hunter2@10.0.0.1 failed"
    gateway = _Gateway(GeminiResult(status="error", error=leak), _answer())

    await _generate_with(monkeypatch, gateway)

    assert leak not in gateway.requests[1].prompt
    assert "hunter2" not in gateway.requests[1].prompt


@pytest.mark.asyncio
async def test_an_attempt_is_charged_at_the_gateways_own_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``core.openai`` retries inside ONE call, so an attempt is several requests."""
    monkeypatch.setattr(settings.deepseek, "max_retries", 2)
    monkeypatch.setattr(settings.neuroshilling, "llm_max_attempts", 3)
    monkeypatch.setattr(settings.neuroshilling, "max_llm_calls_per_day", 9)
    gateway = _Gateway(GeminiResult(status="error"), GeminiResult(status="error"))

    await _generate_with(monkeypatch, gateway)

    # Three attempts, each worth ``max_retries + 1`` HTTP requests.
    assert len(gateway.requests) == 3
    assert _state.at_daily_llm_cap() is True


@pytest.mark.asyncio
async def test_the_budget_running_out_mid_generation_stops_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is re-read every pass: nothing reserved it when the click was let in."""
    monkeypatch.setattr(settings.deepseek, "max_retries", 0)
    monkeypatch.setattr(settings.neuroshilling, "llm_max_attempts", 5)
    monkeypatch.setattr(settings.neuroshilling, "max_llm_calls_per_day", 2)
    gateway = _Gateway(GeminiResult(status="error"), GeminiResult(status="error"))

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is None
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
async def test_a_truncated_answer_shrinks_the_ask_rather_than_repeating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same ask under the same token cap runs out in the same place."""
    cut = GeminiResult(status="truncated", error="Truncated: hit max_tokens")
    gateway = _Gateway(cut, _answer())

    draft = await _generate_with(monkeypatch, gateway, step_count=8)

    assert draft is not None
    assert "exactly 8 steps" in gateway.requests[0].prompt
    assert "exactly 4 steps" in gateway.requests[1].prompt
    assert "cut off mid-answer" in gateway.requests[1].prompt


@pytest.mark.asyncio
async def test_an_unset_key_asks_nothing_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.deepseek, "api_key", "")
    gateway = _Gateway(_answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is None
    assert gateway.requests == []
    assert _state.at_daily_llm_cap() is False


@pytest.mark.asyncio
async def test_a_forward_link_is_repaired_rather_than_re_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link is decoration; burning a paid call to fix it costs more than losing it."""
    forward = _answer(
        steps=[
            {"speaker_id": 1, "text": "first", "reply_to_index": 1, "reaction": None},
            {"speaker_id": 2, "text": "second", "reply_to_index": 1, "reaction": None},
        ],
    )
    gateway = _Gateway(forward)

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert len(gateway.requests) == 1
    assert [step.reply_to_position for step in draft.steps] == [None, None]


@pytest.mark.asyncio
async def test_an_empty_reply_is_dropped_and_the_rest_renumbered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sparse = _answer(
        steps=[
            {"speaker_id": 1, "text": "first", "reply_to_index": None, "reaction": None},
            {"speaker_id": 2, "text": "   ", "reply_to_index": 0, "reaction": None},
            {"speaker_id": 2, "text": "third", "reply_to_index": 0, "reaction": None},
        ],
    )

    draft = await _generate_with(monkeypatch, _Gateway(sparse))

    assert draft is not None
    assert [step.text for step in draft.steps] == ["first", "third"]
    # "third" still answers "first", which is position 1 after the renumbering.
    assert draft.steps[1].reply_to_position == 1


@pytest.mark.asyncio
async def test_a_reaction_becomes_a_reaction_step_pointing_at_its_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reacting = _answer(
        steps=[
            {"speaker_id": 1, "text": "first", "reply_to_index": None, "reaction": None},
            {"speaker_id": 2, "text": "", "reply_to_index": 0, "reaction": "\U0001f525"},
        ],
    )

    draft = await _generate_with(monkeypatch, _Gateway(reacting))

    assert draft is not None
    assert draft.steps[1].kind == "reaction"
    assert draft.steps[1].emoji == "\U0001f525"
    assert draft.steps[1].target_position == 1
    assert draft.steps[1].text == ""


@pytest.mark.asyncio
async def test_a_reaction_with_nothing_to_react_to_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dangling = _answer(
        steps=[
            {"speaker_id": 1, "text": "", "reply_to_index": None, "reaction": "\U0001f525"},
            {"speaker_id": 2, "text": "first", "reply_to_index": None, "reaction": None},
        ],
    )

    draft = await _generate_with(monkeypatch, _Gateway(dangling))

    assert draft is not None
    assert [step.kind for step in draft.steps] == ["message"]


@pytest.mark.asyncio
async def test_an_all_reaction_answer_is_re_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dialogue of nothing but reactions says nothing at all."""
    silent = _answer(
        steps=[
            {"speaker_id": 1, "text": "", "reply_to_index": None, "reaction": "\U0001f525"},
        ],
    )
    gateway = _Gateway(silent, _answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert "at least one step must be a reply" in gateway.requests[1].prompt


@pytest.mark.asyncio
async def test_a_speaker_beyond_the_cast_is_dropped_rather_than_re_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repairable, so it must not cost a paid retry — the module's own rule."""
    stranger = _answer(
        steps=[
            {"speaker_id": 7, "text": "who?", "reply_to_index": None, "reaction": None},
            {"speaker_id": 1, "text": "first", "reply_to_index": None, "reaction": None},
        ],
    )
    gateway = _Gateway(stranger)

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert len(gateway.requests) == 1
    assert [step.text for step in draft.steps] == ["first"]


@pytest.mark.asyncio
async def test_no_steps_at_all_is_re_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = _Gateway(_answer(steps=[]), _answer())

    draft = await _generate_with(monkeypatch, gateway)

    assert draft is not None
    assert "steps was empty" in gateway.requests[1].prompt
