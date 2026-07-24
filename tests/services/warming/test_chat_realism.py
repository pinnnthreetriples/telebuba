"""Phase 2 realism tests for the inter-account chat layer.

Covers history-aware prompts, read receipts, per-account typing tempo, the
near-duplicate gate and the orphan-inbox fix. Split from test_chat.py to keep
both modules under the 700-line cap.
"""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    create_account,
    latest_unreplied_for,
    load_warming_settings,
    record_dialogue_message,
    replace_dialogue_pairs,
    update_account_from_session_check,
)
from schemas.accounts import AccountCreate, AccountRead
from schemas.dialogues import DialogueMessage
from schemas.gemini import GeminiRequest, GeminiResult
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from services.warming import _seams
from services.warming._chat import (
    _account_typing_wpm,
    _generate_chat_text,
    _maybe_inter_account_chat,
    _reply_to_partner,
)
from tests.services.warming._support import (
    _seed_channel,
    _seed_two_warming_accounts,
    _set_settings,
    fetch_account_helper,
)


async def _seed_pair_with_incoming(text: str = "как дела?") -> DialogueMessage:
    """acc-a ↔ acc-b paired; acc-b sent acc-a ``text`` awaiting a reply."""
    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-b",
            session_path="acc-b",
            status="alive",
            is_temporary=False,
            user_id=42,
        ),
    )
    await replace_dialogue_pairs([("acc-a", "acc-b")])
    await record_dialogue_message("acc-b", "acc-a", text)
    incoming = await latest_unreplied_for("acc-a")
    assert incoming is not None
    return incoming


async def _accounts_map() -> dict[str, AccountRead]:
    accounts: dict[str, AccountRead] = {}
    for account_id in ("acc-a", "acc-b"):
        account = await fetch_account_helper(account_id)
        assert account is not None
        accounts[account_id] = account
    return accounts


async def _ok_execute(account_id: str, action: TelegramAction) -> ActionResult:
    return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)


@pytest.mark.asyncio
async def test_history_is_injected_into_reply_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = await _seed_pair_with_incoming()
    secret = await load_warming_settings()

    prompts: list[str] = []

    async def capture_gen(request: GeminiRequest) -> GeminiResult:
        prompts.append(request.prompt)
        return GeminiResult(status="ok", text="ну норм, а у тебя как?")

    async def fake_history(key: str, _limit: int) -> list[DialogueMessage]:
        return [
            DialogueMessage(
                id=1,
                pair_key=key,
                from_account="acc-b",
                to_account="acc-a",
                text="привет!",
                created_at="2026-01-01T00:00:00+00:00",
                replied=True,
            ),
            DialogueMessage(
                id=2,
                pair_key=key,
                from_account="acc-a",
                to_account="acc-b",
                text="о, здорово",
                created_at="2026-01-01T00:01:00+00:00",
                replied=True,
            ),
            DialogueMessage(
                id=3,
                pair_key=key,
                from_account="acc-b",
                to_account="acc-a",
                text="как дела?",
                created_at="2026-01-01T00:02:00+00:00",
                replied=False,
            ),
        ]

    monkeypatch.setattr(_seams, "generate_text", capture_gen)
    monkeypatch.setattr(_seams, "execute", _ok_execute)
    monkeypatch.setattr("services.warming._chat.recent_pair_messages", fake_history)

    result = await _reply_to_partner("acc-a", incoming, secret, await _accounts_map())

    assert result.messages_sent == 1
    assert prompts
    prompt = prompts[0]
    # Transcript labelled from the sender's point of view.
    assert "Собеседник: привет!" in prompt
    assert "Я: о, здорово" in prompt
    assert "Собеседник: как дела?" in prompt


@pytest.mark.asyncio
async def test_reply_marks_read_then_sleeps_then_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = await _seed_pair_with_incoming()
    secret = await load_warming_settings()

    sequence: list[str] = []

    async def rec_execute(account_id: str, action: TelegramAction) -> ActionResult:
        sequence.append(action.action_type)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    slept: list[float] = []

    async def rec_sleep(seconds: float) -> None:
        sequence.append("sleep")
        slept.append(seconds)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="привет обратно")

    monkeypatch.setattr(_seams, "execute", rec_execute)
    monkeypatch.setattr(_seams, "sleep", rec_sleep)
    monkeypatch.setattr(_seams, "generate_text", gen)

    result = await _reply_to_partner("acc-a", incoming, secret, await _accounts_map())

    assert result.messages_sent == 1
    # mark read → delay → send, in that order.
    assert sequence == ["mark_dm_read", "sleep", "send_dm"]
    assert len(slept) == 1


@pytest.mark.asyncio
async def test_reply_read_ack_failure_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = await _seed_pair_with_incoming()
    secret = await load_warming_settings()

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        status = "failed" if action.action_type == "mark_dm_read" else "ok"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="всё ок")

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams, "generate_text", gen)

    result = await _reply_to_partner("acc-a", incoming, secret, await _accounts_map())

    # A failed read-ack does not count as a dialogue failure — the reply still sends.
    assert result.messages_sent == 1
    assert result.failures == 0


@pytest.mark.asyncio
async def test_reply_carries_per_account_typing_wpm(monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = await _seed_pair_with_incoming()
    secret = await load_warming_settings()

    sent: list[TelegramAction] = []

    async def capture(account_id: str, action: TelegramAction) -> ActionResult:
        sent.append(action)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="ответ")

    monkeypatch.setattr(_seams, "execute", capture)
    monkeypatch.setattr(_seams, "generate_text", gen)

    await _reply_to_partner("acc-a", incoming, secret, await _accounts_map())

    dms = [a for a in sent if a.action_type == "send_dm"]
    assert dms
    wpm = dms[0].typing_wpm
    assert wpm is not None
    assert settings.warming.typing_wpm_min <= wpm <= settings.warming.typing_wpm_max


def test_account_typing_wpm_is_stable_and_distinct() -> None:
    lo, hi = settings.warming.typing_wpm_min, settings.warming.typing_wpm_max
    # Same id → same WPM across calls (stable).
    assert _account_typing_wpm("acc-1") == _account_typing_wpm("acc-1")
    # In range.
    assert lo <= _account_typing_wpm("acc-1") <= hi
    # At least two ids differ (distinct across the fleet).
    values = {_account_typing_wpm(f"acc-{i}") for i in range(20)}
    assert len(values) > 1


@pytest.mark.asyncio
async def test_similarity_gate_rejects_near_duplicate_then_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = await load_warming_settings()
    # First candidate differs only by punctuation/spacing → near-duplicate; the
    # second is genuinely different and must pass.
    candidates = iter(["привет, как дела!", "слушай, а ты видел ту новость сегодня"])

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text=next(candidates))

    monkeypatch.setattr(_seams, "generate_text", gen)

    result = await _generate_chat_text(
        "acc-x",
        secret,
        recent_texts=["привет как дела"],
    )

    assert result.text == "слушай, а ты видел ту новость сегодня"


@pytest.mark.asyncio
async def test_similarity_gate_exhausts_when_all_near_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = await load_warming_settings()

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="привет, как дела?")

    monkeypatch.setattr(_seams, "generate_text", gen)

    result = await _generate_chat_text(
        "acc-y",
        secret,
        recent_texts=["привет как дела"],
    )

    assert result.text is None
    assert result.failure_reason == "chat_too_similar"


@pytest.mark.asyncio
async def test_orphan_non_partner_message_is_marked_replied_then_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()  # acc-1 ↔ acc-2 paired; acc-2 user_id 999
    # An ex-partner (acc-3, not in acc-1's current partners) left a dangling
    # unreplied message — it must not shadow the inbox forever.
    await record_dialogue_message("acc-3", "acc-1", "старое сообщение", replied=False)

    sent: list[TelegramAction] = []

    async def capture(account_id: str, action: TelegramAction) -> ActionResult:
        sent.append(action)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="привет, давно не общались")

    monkeypatch.setattr(_seams, "execute", capture)
    monkeypatch.setattr(_seams, "generate_text", gen)

    secret = await load_warming_settings()
    result = await _maybe_inter_account_chat("acc-1", secret)

    # The orphan is marked replied (no longer pending for acc-1) and the opener
    # path ran against the current partner.
    assert await latest_unreplied_for("acc-1") is None
    assert result.messages_sent == 1
    dms = [a for a in sent if a.action_type == "send_dm"]
    assert dms
    assert dms[0].user_id == 999
