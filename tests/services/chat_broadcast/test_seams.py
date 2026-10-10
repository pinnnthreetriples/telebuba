"""The seams: the generation fence, the pacer, the gateway domain, the captcha wrapper."""

from __future__ import annotations

import pytest

from schemas.gemini import GeminiRequest, GeminiResult
from schemas.telegram_actions import ActionResult, BroadcastSendMessage, ListWritableGroups
from schemas.telegram_actions_broadcast import WritableGroupsResult
from services.chat_broadcast import _seams, _state

_REQUEST = GeminiRequest(api_key="k", prompt="p", model="m", temperature=0.5, max_output_tokens=10)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def _execute(account_id: str, _action: object, *, domain: str) -> ActionResult:
        seen.append(f"execute:{account_id}:{domain}")
        return ActionResult(status="ok", action_type="broadcast_send_message", account_id="a1")

    async def _read(account_id: str, _action: object) -> WritableGroupsResult:
        seen.append(f"read:{account_id}")
        return WritableGroupsResult(groups=[])

    async def _slot(key: str, _gap: float) -> None:
        seen.append(f"slot:{key}")

    async def _generate(_request: GeminiRequest) -> GeminiResult:
        seen.append("llm")
        return GeminiResult(status="ok", text="x")

    monkeypatch.setattr(_seams, "_gateway_execute", _execute)
    monkeypatch.setattr(_seams, "_gateway_execute_read", _read)
    monkeypatch.setattr(_seams, "await_send_slot", _slot)
    monkeypatch.setattr(_seams, "_generate_text", _generate)
    monkeypatch.setattr(_seams, "_generate_text_deepseek", _generate)
    return seen


@pytest.mark.asyncio
async def test_calls_are_paced_and_carry_the_domain(calls: list[str]) -> None:
    await _seams.execute("a1", BroadcastSendMessage(chat="1", text="hi"))
    await _seams.execute_read("a1", ListWritableGroups())
    await _seams.generate_text(_REQUEST)
    await _seams.generate_text_deepseek(_REQUEST)

    assert calls == ["slot:a1", "execute:a1:chat_broadcast", "read:a1", "llm", "llm"]


@pytest.mark.asyncio
async def test_a_revoked_generation_cannot_reach_telegram(calls: list[str]) -> None:
    generation = _state.begin_run("c1", "r1")
    _state.revoke_run("c1")

    with _seams.run_scope(lambda: _state.run_is_current("c1", generation)):
        with pytest.raises(_seams.ChatBroadcastRunRevokedError):
            await _seams.execute("a1", BroadcastSendMessage(chat="1", text="hi"))
        with pytest.raises(_seams.ChatBroadcastRunRevokedError):
            await _seams.generate_text(_REQUEST)
    assert calls == []


@pytest.mark.asyncio
async def test_the_captcha_wrapper_turns_a_crash_into_a_failed_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(*_args: object) -> str:
        raise RuntimeError

    async def _solved(*_args: object) -> str:
        return "solved"

    monkeypatch.setattr("services.neurocomment.solve_join_challenge", _boom)
    assert await _seams.solve_challenge("a1", "@chat", 5) == "failed"
    monkeypatch.setattr("services.neurocomment.solve_join_challenge", _solved)
    assert await _seams.solve_challenge("a1", "@chat", 5) == "solved"


def test_run_ownership_and_claims() -> None:
    assert _state.claim_settlement("c1", "r1")
    _state.begin_run("c1", "r1")
    assert not _state.claim_settlement("c1", "r2")
    assert not _state.revoke_run_if_current("c1", "r2")
    _state.abandon_run("c1", "r1")
    assert _state.revoke_run_if_current("c1", "r2")
    assert _state.try_claim_edit("c1")
    assert not _state.try_claim_start("c1")
    assert _state.try_claim_edit("c1")
    _state.finish_edit("c1")
    _state.finish_edit("c1")
    assert _state.try_claim_start("c1")
    assert _state.start_in_flight("c1")
    assert not _state.try_claim_edit("c1")
    _state.finish_start("c1")
    assert _state.first_fallback("r1", "no_key")
    assert not _state.first_fallback("r1", "no_key")
