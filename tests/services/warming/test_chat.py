"""Warming tests split from the former service test module: test_chat.py."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import (
    create_account,
    latest_unreplied_for,
    load_warming_settings,
    purge_sent_hashes_older_than,
    record_dialogue_message,
    update_account_from_session_check,
    upsert_warming_state,
)
from schemas.accounts import AccountCreate, AccountRead
from schemas.gemini import GeminiResult
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import (
    WarmingCycleRequest,
    WarmingStateWrite,
)
from services import warming
from services.content import register_sent
from services.dialogues import assign_pairs
from services.warming import _seams
from tests.services.warming._support import (
    _account,
    _Recorder,
    _resolve,
    _seed_channel,
    _seed_two_warming_accounts,
    _set_settings,
    fetch_account_helper,
)


@pytest.mark.asyncio
async def test_cycle_dm_gate_honours_request_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # The loop passes a trust+readiness-aware dm_allowed into the cycle; when it
    # is False the cycle must not DM even if age/chat/key would otherwise allow
    # it (audit П11). None (direct callers) keeps the age-only behaviour.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="hi there")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", dm_allowed=False),
    )

    assert result.messages_sent == 0


@pytest.mark.asyncio
async def test_cycle_sends_inter_account_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="hi there")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 1
    dm_actions = [a for _id, a in recorder.actions if a.action_type == "send_dm"]
    assert dm_actions
    assert dm_actions[0].user_id == 999


@pytest.mark.asyncio
async def test_cycle_skips_dm_when_persona_roll_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Even with DM fully permitted (aged, paired, pending reply), a persona roll
    # above the persona's DM probability skips the chat this session — the
    # persona's frequency lever, on top of the age/trust/settings gate.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.99)  # above every persona DM prob
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await record_dialogue_message("acc-2", "acc-1", "привет!", replied=False)

    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", activity_persona="calm"),
    )

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_dm_when_generation_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="error", error="quota")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_dm_without_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_dm_when_peer_has_no_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    # Two warming accounts but the peer was never session-checked → user_id is None.
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-2", state="active"))
    await assign_pairs()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_dm_on_duplicate_content(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="hi there")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await register_sent("hi there")  # already sent → every attempt is a duplicate

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_dm_on_forbidden_content(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    monkeypatch.setattr(settings.warming, "content_forbidden_words", ["купить"])

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="купить дёшево прямо сейчас")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_replies_to_pending_partner_message(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="о, привет, как сам?")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()  # acc-1 ↔ acc-2 paired; acc-2 has user_id 999
    # acc-2 has sent acc-1 a message that is awaiting a reply.
    await record_dialogue_message("acc-2", "acc-1", "привет!", replied=False)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 1
    dms = [action for _id, action in recorder.actions if action.action_type == "send_dm"]
    assert dms
    assert dms[0].user_id == 999
    # the incoming message is now answered → not replied again
    assert await latest_unreplied_for("acc-1") is None


@pytest.mark.asyncio
async def test_dialogue_reply_chains_for_multi_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="ага, у меня норм, а у тебя?")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await record_dialogue_message("acc-2", "acc-1", "привет!", replied=False)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 1
    # acc-1's reply is now pending for acc-2 → the conversation can continue
    pending = await latest_unreplied_for("acc-2")
    assert pending is not None
    assert pending.from_account == "acc-1"


@pytest.mark.asyncio
async def test_conversation_fades_after_max_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    monkeypatch.setattr(settings.warming, "dialogue_max_turns", 1)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    # The pair has already hit the turn cap; the incoming should fade, not reply.
    await record_dialogue_message("acc-2", "acc-1", "привет!", replied=False)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()
    # the thread is ended (incoming marked replied), no new pending message
    assert await latest_unreplied_for("acc-1") is None


@pytest.mark.asyncio
async def test_sanitize_chat_text_strips_control_and_caps_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "chat_message_max_chars", 20)
    monkeypatch.setattr(settings.warming, "chat_message_max_lines", 2)
    raw = "  hi\x07 there\n\nsecond line\nthird line should be dropped  "
    result = warming._sanitize_chat_text(raw)
    assert result is not None
    assert "\x07" not in result
    assert result.count("\n") <= 1
    assert len(result) <= 20


@pytest.mark.asyncio
async def test_sanitize_chat_text_returns_none_for_blank() -> None:
    assert warming._sanitize_chat_text("\x00\x01\n  ") is None


@pytest.mark.asyncio
async def test_cycle_diagnostics_chat_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)  # persona DM roll always fires
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    await _seed_two_warming_accounts()
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="error", text=None)

    monkeypatch.setattr(_seams, "generate_text", fake_generate)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.last_failed_action == "generate_chat_text"


@pytest.mark.asyncio
async def test_reply_flood_releases_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """F6: send flood on reply leaves the incoming message claimable next cycle."""
    from core.db import (  # noqa: PLC0415
        latest_unreplied_for,
        record_dialogue_message,
        replace_dialogue_pairs,
    )

    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    acc_b_session = await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-b",
            session_path="acc-b",
            status="alive",
            is_temporary=False,
            user_id=42,
            phone=None,
            username=None,
            first_name=None,
            last_name=None,
        ),
    )
    assert acc_b_session.user_id == 42
    await replace_dialogue_pairs([("acc-a", "acc-b")])
    # acc-b sent a message to acc-a; acc-a will try to reply but get flooded.
    await record_dialogue_message("acc-b", "acc-a", "hi there")

    async def flood_execute(account_id: str, action: TelegramAction) -> ActionResult:
        return ActionResult(
            status="flood_wait",
            action_type=action.action_type,
            account_id=account_id,
            flood_wait_seconds=60,
        )

    monkeypatch.setattr(_seams, "execute", flood_execute)
    monkeypatch.setattr(
        _seams,
        "generate_text",
        lambda req: _resolve(GeminiResult(status="ok", text="ok-reply")),  # noqa: ARG005
    )

    incoming = await latest_unreplied_for("acc-a")
    assert incoming is not None
    secret = await load_warming_settings()
    accounts_map = {
        "acc-a": await fetch_account_helper("acc-a"),
        "acc-b": await fetch_account_helper("acc-b"),
    }
    from services.warming._chat import _reply_to_partner  # noqa: PLC0415

    result = await _reply_to_partner("acc-a", incoming, secret, accounts_map)
    assert result.flood_result is not None
    # The incoming row should still be claimable.
    still_pending = await latest_unreplied_for("acc-a")
    assert still_pending is not None
    assert still_pending.id == incoming.id


@pytest.mark.asyncio
async def test_try_reserve_sent_hash_concurrent_only_one_wins() -> None:
    """F7: parallel reservers of the same hash never both observe an empty window."""
    from core.db import try_reserve_sent_hash  # noqa: PLC0415

    # Warm up the engine on this thread before fanning out — the gather below
    # spawns 8 threads via asyncio.to_thread, which would otherwise race on
    # ``_get_engine`` + ``_metadata.create_all`` and leave some threads talking
    # to a DB where the table did not yet exist.
    await purge_sent_hashes_older_than("1900-01-01T00:00:00+00:00")

    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    results = await asyncio.gather(*(try_reserve_sent_hash("shared-hash", since) for _ in range(8)))
    assert sum(1 for r in results if r) == 1
    assert sum(1 for r in results if not r) == 7


@pytest.mark.asyncio
async def test_open_with_partner_deterministic_tiebreak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F8: smaller account_id opens; larger waits, preventing crossing DMs."""
    from services.warming._chat import _open_with_partner  # noqa: PLC0415

    # Two accounts; "alpha" < "bravo" lexicographically.
    accounts: dict[str, AccountRead] = {
        "alpha": AccountRead(
            account_id="alpha",
            label=None,
            session_name="alpha",
            status="alive",
            user_id=1,
            phone=None,
            username=None,
            first_name=None,
            last_name=None,
            bio=None,
            last_checked_at=None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        "bravo": AccountRead(
            account_id="bravo",
            label=None,
            session_name="bravo",
            status="alive",
            user_id=2,
            phone=None,
            username=None,
            first_name=None,
            last_name=None,
            bio=None,
            last_checked_at=None,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
    }
    secret = await load_warming_settings()

    sent: list[tuple[str, TelegramAction]] = []

    async def capture(account_id: str, action: TelegramAction) -> ActionResult:
        sent.append((account_id, action))
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def gen(req: object) -> GeminiResult:  # noqa: ARG001
        return GeminiResult(status="ok", text="howdy")

    monkeypatch.setattr(_seams, "execute", capture)
    monkeypatch.setattr(_seams, "generate_text", gen)

    # bravo is the larger id; its opener attempt must be a no-op.
    bravo_result = await _open_with_partner("bravo", ["alpha"], secret, accounts)
    assert bravo_result.messages_sent == 0
    assert sent == []

    # alpha opens normally.
    alpha_result = await _open_with_partner("alpha", ["bravo"], secret, accounts)
    assert alpha_result.messages_sent == 1
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_reply_flood_does_not_block_same_text_retry_as_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2.6: a reply that floods must not lock its own text out of the dedup window.

    The pre-fix path reserved the text via try_reserve_sent inside Gemini
    generation, but on flood/peer_flood the reservation stayed. A second cycle
    that generated the *same* reply would be filtered out as duplicate and the
    incoming message could never actually get answered. Fix: release the
    reservation on every non-ok send branch.
    """
    from core.db import (  # noqa: PLC0415
        latest_unreplied_for,
        record_dialogue_message,
        replace_dialogue_pairs,
    )

    await create_account(AccountCreate(account_id="acc-a"))
    await create_account(AccountCreate(account_id="acc-b"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-b",
            session_path="acc-b",
            status="alive",
            is_temporary=False,
            user_id=42,
            phone=None,
            username=None,
            first_name=None,
            last_name=None,
        ),
    )
    await replace_dialogue_pairs([("acc-a", "acc-b")])
    await record_dialogue_message("acc-b", "acc-a", "hi there")

    # Gemini deterministically returns the same line — the same content hash
    # would normally be locked for the entire dedup window after the first send.
    async def stable_gen(_req: object) -> GeminiResult:
        return GeminiResult(status="ok", text="привет!")

    async def flood_execute(account_id: str, action: TelegramAction) -> ActionResult:
        return ActionResult(
            status="flood_wait",
            action_type=action.action_type,
            account_id=account_id,
            flood_wait_seconds=60,
        )

    monkeypatch.setattr(_seams, "generate_text", stable_gen)
    monkeypatch.setattr(_seams, "execute", flood_execute)

    from services.warming._chat import _reply_to_partner  # noqa: PLC0415

    incoming = await latest_unreplied_for("acc-a")
    assert incoming is not None
    secret = await load_warming_settings()
    accounts_map = {
        "acc-a": await fetch_account_helper("acc-a"),
        "acc-b": await fetch_account_helper("acc-b"),
    }

    first = await _reply_to_partner("acc-a", incoming, secret, accounts_map)
    assert first.flood_result is not None

    # The hash reservation must have been released — running Gemini -> same text
    # path again would see ``chat_duplicate`` if it weren't.
    again_incoming = await latest_unreplied_for("acc-a")
    assert again_incoming is not None
    second = await _reply_to_partner("acc-a", again_incoming, secret, accounts_map)
    # Still flood (we did not change the execute seam), but the failure_reason
    # is ``send_dm`` (the send attempt happened), not ``chat_duplicate``
    # (the dedup gate would have rejected before the send).
    assert second.last_failed_action == "send_dm"


@pytest.mark.asyncio
async def test_open_with_partner_rests_on_a_faded_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opener must not keep sending one-sided DMs to a faded pair (#review)."""
    from services.warming._chat import _open_with_partner  # noqa: PLC0415

    monkeypatch.setattr(settings.warming, "dialogue_max_turns", 1)
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    # The pair has already hit the turn cap within the window -> faded.
    await record_dialogue_message("acc-1", "acc-2", "привет!", replied=False)

    sent: list[tuple[str, TelegramAction]] = []

    async def capture(account_id: str, action: TelegramAction) -> ActionResult:
        sent.append((account_id, action))
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def gen(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="howdy")

    monkeypatch.setattr(_seams, "execute", capture)
    monkeypatch.setattr(_seams, "generate_text", gen)
    secret = await load_warming_settings()
    accounts = {
        "acc-1": _account(account_id="acc-1", user_id=1),
        "acc-2": _account(account_id="acc-2", user_id=2),
    }

    result = await _open_with_partner("acc-1", ["acc-2"], secret, accounts)

    assert result.messages_sent == 0
    assert sent == []  # faded pair -> opener rests, no one-sided DM
