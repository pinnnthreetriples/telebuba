"""Tests for the warming engine (``services.warming``).

Telegram I/O (``execute``) and Gemini (``generate_text``) are patched at the
service boundary so the engine is exercised with no real network or sleeps.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete as sa_delete

from core.config import settings
from core.db import (
    _accounts,
    _get_engine,
    _warming_account_state,
    configure_database,
    create_account,
    fetch_warming_state,
    latest_unreplied_for,
    list_dialogue_pairs,
    purge_dialogue_messages_older_than,
    purge_logs_older_than,
    purge_sent_hashes_older_than,
    record_dialogue_message,
    save_warming_settings,
    update_account_from_session_check,
    update_account_proxy_check,
    upsert_account_proxy,
    upsert_spam_status,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate, AccountRead
from schemas.gemini import GeminiResult
from schemas.proxy import AccountProxyCheckUpdate, AccountProxyUpsert
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingSettingsUpdate,
    WarmingStateRecord,
    WarmingStateWrite,
)
from services import warming
from services.content import register_sent
from services.dialogues import assign_pairs
from services.warming import _loop, _runtime, _seams

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_ZERO_DELAY_FIELDS = (
    "action_delay_min_seconds",
    "action_delay_max_seconds",
    "typing_min_seconds",
    "typing_max_seconds",
    "reading_min_seconds",
    "reading_max_seconds",
    "startup_jitter_max_seconds",
)


class _Recorder:
    """Captures dispatched actions and returns canned results."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, TelegramAction]] = []
        self.flood_on: set[str] = set()
        self.peer_flood_on: set[str] = set()

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        status = "ok"
        if action.action_type in self.flood_on:
            status = "flood_wait"
        elif action.action_type in self.peer_flood_on:
            status = "peer_flood"
        return ActionResult(
            status=status,
            action_type=action.action_type,
            account_id=account_id,
        )

    def types(self) -> list[str]:
        return [action.action_type for _account_id, action in self.actions]


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    # Gemini credentials live in .env (not the DB). Provide a non-empty default
    # so cycle/save tests see has_gemini_key=True without each one mucking with
    # the secret namespace.
    monkeypatch.setattr(settings.gemini, "api_key", "test-key")
    for field in _ZERO_DELAY_FIELDS:
        monkeypatch.setattr(settings.warming, field, 0.0)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 1)
    reset_logging_for_tests()
    setup_logging()
    warming._RUNTIME.clear()
    yield
    warming._RUNTIME.clear()
    reset_logging_for_tests()


async def _seed_channel() -> None:
    await warming.add_channels(AddChannelsRequest(raw="@channel_one"))


async def _set_settings(*, chat: bool, reactions: bool, key: str | None) -> None:
    await save_warming_settings(
        inter_account_chat=chat,
        reactions_enabled=reactions,
        gemini_api_key=key,
    )


# --------------------------------------------------------------------------- #
# Channels & settings
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_add_channels_parses_and_dedupes() -> None:
    # Normalization now strips a leading ``@`` and requires usernames of
    # length 3-32 — pick names that satisfy Telegram's minimum.
    result = await warming.add_channels(
        AddChannelsRequest(raw="@alpha, https://t.me/beta\n@alpha\n  gamma  "),
    )

    assert [channel.channel for channel in result.channels] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_save_settings_returns_masked_view() -> None:
    masked = await warming.save_settings(
        WarmingSettingsUpdate(inter_account_chat=True, reactions_enabled=False, gemini_api_key="k"),
    )

    assert masked.inter_account_chat is True
    assert masked.reactions_enabled is False
    assert masked.has_gemini_key is True
    assert not hasattr(masked, "gemini_api_key")


# --------------------------------------------------------------------------- #
# run_one_cycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cycle_skips_without_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "skipped"
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_cycle_happy_path_joins_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "ok"
    assert result.channels_joined == 1
    assert result.channels_read == 1
    assert result.reactions_sent == 0
    types = recorder.types()
    assert types[0] == "set_online"
    assert "join_channel" in types
    assert "read_channel" in types
    assert types[-1] == "set_online"


@pytest.mark.asyncio
async def test_cycle_reacts_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "reaction_probability", 1.0)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.reactions_sent == 1
    assert "react_to_post" in recorder.types()


@pytest.mark.asyncio
async def test_cycle_stops_on_flood_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.flood_on.add("join_channel")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    assert result.channels_joined == 0
    assert "read_channel" not in recorder.types()


# --------------------------------------------------------------------------- #
# Age-based ramp
# --------------------------------------------------------------------------- #


def _configure_ramp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "ramp_enabled", True)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 3)
    monkeypatch.setattr(settings.warming, "reaction_probability", 0.6)
    monkeypatch.setattr(settings.warming, "ramp_initial_channels_max", 1)
    monkeypatch.setattr(settings.warming, "ramp_initial_reaction_probability", 0.1)
    monkeypatch.setattr(settings.warming, "ramp_full_age_hours", 192.0)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 36.0)


def test_compute_intensity_is_quiet_for_a_fresh_account(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_ramp(monkeypatch)
    fresh = warming.compute_intensity(0.0)
    assert fresh.channels_max == 1
    assert fresh.reaction_probability == pytest.approx(0.1)
    assert fresh.dm_allowed is False


def test_compute_intensity_reaches_full_intensity_when_aged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_ramp(monkeypatch)
    aged = warming.compute_intensity(500.0)
    assert aged.channels_max == 3
    assert aged.reaction_probability == pytest.approx(0.6)
    assert aged.dm_allowed is True
    # DM unlocks exactly at the cold-start threshold.
    assert warming.compute_intensity(36.0).dm_allowed is True
    assert warming.compute_intensity(35.0).dm_allowed is False


def test_compute_intensity_full_when_ramp_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_ramp(monkeypatch)
    monkeypatch.setattr(settings.warming, "ramp_enabled", False)
    full = warming.compute_intensity(0.0)
    assert full.channels_max == 3
    assert full.reaction_probability == pytest.approx(0.6)
    assert full.dm_allowed is True


@pytest.mark.asyncio
async def test_cycle_skips_dm_for_fresh_account(monkeypatch: pytest.MonkeyPatch) -> None:
    # A freshly created account (age ~0) must not send DMs under the cold-start
    # guard, even with chat enabled and an eligible peer present.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


# --------------------------------------------------------------------------- #
# PEER_FLOOD quarantine
# --------------------------------------------------------------------------- #


def _verdict(account_id: str, status: SpamStatusKind) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=account_id,
        status=status,
        checked_at="2026-06-13T00:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_cycle_reports_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.peer_flood_on.add("join_channel")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "peer_flood"
    assert "read_channel" not in recorder.types()


@pytest.mark.asyncio
async def test_loop_iteration_quarantines_on_peer_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.peer_flood_on.add("join_channel")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "quarantine"


@pytest.mark.asyncio
async def test_quarantine_recovers_when_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", quarantine_count=1),
    )

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "clean")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)

    result = await warming.run_loop_iteration("acc-1")

    assert result.detail == "recovered"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "sleeping"
    assert state.quarantine_count == 0


@pytest.mark.asyncio
async def test_quarantine_extends_when_still_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", 3)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", quarantine_count=0),
    )

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "limited")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)

    await warming.run_loop_iteration("acc-1")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "quarantine"
    assert state.quarantine_count == 1


@pytest.mark.asyncio
async def test_quarantine_exhausts_to_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "quarantine_max_repeats", 3)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="quarantine", quarantine_count=2),
    )

    async def fake_refresh(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        return _verdict(account_id, "limited")

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_refresh)

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "error"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "error"


# --------------------------------------------------------------------------- #
# Inter-account chat
# --------------------------------------------------------------------------- #


async def _seed_two_warming_accounts() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-2",
            session_path="acc-2",
            status="alive",
            is_temporary=False,
            user_id=999,
        ),
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-2", state="active"))
    await assign_pairs()


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


# --------------------------------------------------------------------------- #
# Board & start/stop
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_load_board_splits_idle_and_warming() -> None:
    await create_account(AccountCreate(account_id="acc-idle"))
    await create_account(AccountCreate(account_id="acc-warming"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-warming", state="active"))

    board = await warming.load_board()

    assert {card.account_id for card in board.idle} == {"acc-idle"}
    assert {card.account_id for card in board.warming} == {"acc-warming"}
    assert board.active_count == 1


@pytest.mark.asyncio
async def test_load_board_enriches_cards_and_summary() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1",
            session_path="acc-1",
            status="alive",
            is_temporary=False,
        ),
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="clean",
            checked_at="2026-06-13T00:00:00+00:00",
        ),
    )

    board = await warming.load_board()

    card = next(c for c in (*board.idle, *board.warming) if c.account_id == "acc-1")
    assert card.trust_score is not None
    assert card.trust_band is not None
    assert card.spam_status == "clean"
    assert card.age_hours is not None
    assert board.summary.total == 1


@pytest.mark.asyncio
async def test_start_and_stop_warming_manage_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )

    started = await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert started.state == "active"
    task = warming._RUNTIME["acc-1"]
    assert not task.done()

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))
    assert stopped.state == "idle"
    assert "acc-1" not in warming._RUNTIME
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_warming_without_running_task_is_safe() -> None:
    await create_account(AccountCreate(account_id="acc-1"))

    stopped = await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))

    assert stopped.state == "idle"


@pytest.mark.asyncio
async def test_stop_unknown_account_returns_idle_card() -> None:
    # No account row exists — _current_card falls back to the id as the label.
    stopped = await warming.stop_warming(StopWarmingRequest(account_id="ghost"))

    assert stopped.account_id == "ghost"
    assert stopped.label == "ghost"
    assert stopped.state == "idle"


@pytest.mark.asyncio
async def test_list_and_remove_channels_roundtrip() -> None:
    await warming.add_channels(AddChannelsRequest(raw="@alpha\n@beta"))

    listed = await warming.list_channels()
    assert {channel.channel for channel in listed.channels} == {"alpha", "beta"}

    remaining = await warming.remove_channel(RemoveChannelRequest(channel="alpha"))
    assert [channel.channel for channel in remaining.channels] == ["beta"]


@pytest.mark.asyncio
async def test_load_settings_masks_key() -> None:
    await save_warming_settings(
        inter_account_chat=True,
        reactions_enabled=False,
        gemini_api_key="secret",
    )

    masked = await warming.load_settings()

    assert masked.has_gemini_key is True
    assert masked.inter_account_chat is True
    assert masked.reactions_enabled is False


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


# --------------------------------------------------------------------------- #
# Lifecycle, failed handling, sanitization, runtime reconcile
# --------------------------------------------------------------------------- #


class _StatusRecorder:
    """Like ``_Recorder`` but lets each action_type carry its own status."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, TelegramAction]] = []
        self.status_by_type: dict[str, str] = {}
        self.raise_on: set[str] = set()
        self.flood_seconds_by_type: dict[str, int] = {}

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        if action.action_type in self.raise_on:
            msg = f"boom-{action.action_type}"
            raise RuntimeError(msg)
        status = self.status_by_type.get(action.action_type, "ok")
        flood = self.flood_seconds_by_type.get(action.action_type)
        return ActionResult.model_validate(
            {
                "status": status,
                "action_type": action.action_type,
                "account_id": account_id,
                "flood_wait_seconds": flood,
            },
        )

    def types(self) -> list[str]:
        return [a.action_type for _id, a in self.actions]


@pytest.mark.asyncio
async def test_cycle_marks_failed_when_every_action_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _StatusRecorder()
    recorder.status_by_type = {
        "set_online": "ok",
        "join_channel": "failed",
        "read_channel": "failed",
    }
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "failed"
    assert result.failures >= 2
    assert result.last_failed_action is not None
    # set_online(False) must still run even after all-fail path.
    assert recorder.types().count("set_online") == 2


@pytest.mark.asyncio
async def test_cycle_sets_offline_even_when_inner_step_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _StatusRecorder()
    recorder.raise_on = {"join_channel"}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    with pytest.raises(RuntimeError):
        await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    # finally block must have run the second set_online.
    online_actions = [a for _id, a in recorder.actions if a.action_type == "set_online"]
    assert len(online_actions) == 2
    assert online_actions[0].online is True
    assert online_actions[1].online is False


@pytest.mark.asyncio
async def test_cycle_propagates_flood_wait_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _StatusRecorder()
    recorder.status_by_type = {"join_channel": "flood_wait"}
    recorder.flood_seconds_by_type = {"join_channel": 42}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 42
    assert result.flood_wait_until is not None


@pytest.mark.asyncio
async def test_start_warming_unknown_account_raises() -> None:
    with pytest.raises(warming.UnknownAccountError):
        await warming.start_warming(StartWarmingRequest(account_id="ghost"))


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
async def test_save_settings_ignores_gemini_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """UI inputs for Gemini key/model are accepted for compat but never persisted."""
    monkeypatch.setattr(settings.gemini, "api_key", "env-key")
    monkeypatch.setattr(settings.gemini, "model", "gemini-from-env")

    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            gemini_api_key="ignored",
            gemini_model="ignored-model",
            clear_gemini_key=True,
        ),
    )
    # has_gemini_key reflects the env, not what the UI tried to save.
    assert masked.has_gemini_key is True
    assert masked.gemini_model == "gemini-from-env"


@pytest.mark.asyncio
async def test_save_settings_model_is_env_managed(monkeypatch: pytest.MonkeyPatch) -> None:
    """gemini_model on save is ignored; the value comes from settings.gemini.model."""
    monkeypatch.setattr(settings.gemini, "model", "gemini-2.5-pro-from-env")

    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            gemini_model="user-typed-other-name",
        ),
    )
    assert masked.gemini_model == "gemini-2.5-pro-from-env"


@pytest.mark.asyncio
async def test_add_channels_respects_per_add_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "max_channels_per_add", 2)
    raw = "@first_one @second_one @third_one @fourth_one"

    result = await warming.add_channels(AddChannelsRequest(raw=raw))

    assert len(result.channels) == 2


@pytest.mark.asyncio
async def test_add_channels_respects_total_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "max_channels_total", 2)
    await warming.add_channels(AddChannelsRequest(raw="@alpha @beta"))

    result = await warming.add_channels(AddChannelsRequest(raw="@gamma"))

    assert len(result.channels) == 2


@pytest.mark.asyncio
async def test_add_channels_rejects_garbage_tokens() -> None:
    result = await warming.add_channels(
        AddChannelsRequest(raw="!!! a/b/c not-a-channel? @ok_one"),
    )
    assert [ch.channel for ch in result.channels] == ["ok_one"]


@pytest.mark.asyncio
async def test_reconcile_warming_runtime_restarts_active_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    async def fake_loop(account_id: str) -> None:
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-1" in warming._RUNTIME
    # Give the loop a single scheduling tick so it actually starts.
    await asyncio.sleep(0)
    assert "acc-1" in started


@pytest.mark.asyncio
async def test_reconcile_warming_runtime_skips_error_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Error accounts must not be auto-resurrected on restart; user has to act."""
    started: list[str] = []

    async def fake_loop(account_id: str) -> None:
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-broken"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-broken", state="error"))

    await warming.reconcile_warming_runtime()

    assert "acc-broken" not in warming._RUNTIME
    await asyncio.sleep(0)
    assert "acc-broken" not in started


@pytest.mark.asyncio
async def test_reconcile_warming_runtime_builds_dialogue_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inter-account chat needs pairs — reconcile must build the graph on startup."""

    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    for account_id in ("acc-1", "acc-2", "acc-3"):
        await _seed_ready_account(account_id)
        await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="active"))
    assert (await list_dialogue_pairs()) == []

    await warming.reconcile_warming_runtime()

    pairs = await list_dialogue_pairs()
    assert pairs, "reconcile must produce dialogue pairs so inter-account chat works"


@pytest.mark.asyncio
async def test_reconcile_purges_stale_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconcile must run retention so logs/dialogues/hashes don't grow forever."""
    monkeypatch.setattr(settings.warming, "log_retention_days", 30.0)
    monkeypatch.setattr(settings.warming, "dialogue_message_retention_days", 90.0)
    monkeypatch.setattr(settings.warming, "sent_hash_retention_days", 14.0)

    calls: list[str] = []

    async def make_recorder(name: str) -> object:
        async def fake(_cutoff: str) -> int:
            calls.append(name)
            return 0

        return fake

    monkeypatch.setattr(
        "services.warming._runtime.purge_logs_older_than",
        await make_recorder("logs"),
    )
    monkeypatch.setattr(
        "services.warming._runtime.purge_dialogue_messages_older_than",
        await make_recorder("dialogues"),
    )
    monkeypatch.setattr(
        "services.warming._runtime.purge_sent_hashes_older_than",
        await make_recorder("hashes"),
    )

    await warming.reconcile_warming_runtime()

    assert set(calls) == {"logs", "dialogues", "hashes"}
    # Sanity: the real purge_* functions still work at the repo level.
    assert await purge_logs_older_than("1900-01-01") == 0
    assert await purge_dialogue_messages_older_than("1900-01-01") == 0
    assert await purge_sent_hashes_older_than("1900-01-01") == 0


@pytest.mark.asyncio
async def test_reconcile_marks_orphan_state_rows_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    # Insert state row directly via DB helper, bypassing FK requirement by
    # first creating then deleting the account.
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    with _get_engine().begin() as conn:
        conn.execute(
            sa_delete(_warming_account_state).where(
                _warming_account_state.c.account_id == "acc-1",
            ),
        )
        conn.execute(sa_delete(_accounts).where(_accounts.c.account_id == "acc-1"))

    # Re-insert state directly (the FK would block in normal flow, but tests
    # explicitly probe the orphan path).
    with _get_engine().begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.execute(
            _warming_account_state.insert().values(
                account_id="acc-1",
                state="active",
                cycles_completed=0,
                updated_at="2026-01-01T00:00:00+00:00",
            ),
        )

    await warming.reconcile_warming_runtime()

    # Orphan must not be re-scheduled.
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_shutdown_warming_runtime_cancels_all(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await warming.start_warming(StartWarmingRequest(account_id="acc-2"))
    assert len(warming._RUNTIME) == 2

    await warming.shutdown_warming_runtime()

    assert warming._RUNTIME == {}


@pytest.mark.asyncio
async def test_run_loop_iteration_transitions_to_flood_on_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _StatusRecorder()
    recorder.status_by_type = {"join_channel": "flood_wait"}
    recorder.flood_seconds_by_type = {"join_channel": 17}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 17
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "flood_wait"
    assert record.flood_wait_seconds == 17


# --------------------------------------------------------------------------- #
# Readiness gate + proxy snapshot
# --------------------------------------------------------------------------- #


def _account(**overrides: object) -> AccountRead:
    base: dict[str, object] = {
        "account_id": "acc-1",
        "status": "alive",
        "created_at": "2026-06-12T00:00:00+00:00",
        "updated_at": "2026-06-12T00:00:00+00:00",
    }
    base.update(overrides)
    return AccountRead.model_validate(base)


async def _seed_ready_account(account_id: str = "acc-1") -> None:
    await create_account(AccountCreate(account_id=account_id))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=account_id,
            status="alive",
            is_temporary=False,
            user_id=111,
        ),
    )
    await upsert_account_proxy(
        AccountProxyUpsert(account_id=account_id, proxy_type="socks5", host="1.2.3.4", port=1080),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(
            account_id=account_id,
            status="tcp_working",
            exit_ip="9.9.9.9",
            country_code="US",
        ),
    )
    await _seed_channel()


def test_evaluate_readiness_ready() -> None:
    account = _account(proxy_host="1.2.3.4", proxy_status="tcp_working")
    readiness = warming.evaluate_readiness(account, 3)
    assert readiness.ready is True
    assert readiness.reasons == []


def test_evaluate_readiness_collects_all_blockers() -> None:
    account = _account(status="new")  # no proxy, no channels
    readiness = warming.evaluate_readiness(account, 0)
    assert readiness.ready is False
    assert any("session" in reason for reason in readiness.reasons)
    assert "no proxy" in readiness.reasons
    assert "no channels" in readiness.reasons


def test_evaluate_readiness_flags_failed_proxy() -> None:
    account = _account(proxy_host="1.2.3.4", proxy_status="failed")
    readiness = warming.evaluate_readiness(account, 1)
    assert "proxy failed" in readiness.reasons


def test_proxy_snapshot_none_without_proxy() -> None:
    assert warming._proxy_snapshot(_account()) is None


def test_proxy_snapshot_formats_with_country() -> None:
    account = _account(
        proxy_type="socks5",
        proxy_host="1.2.3.4",
        proxy_port=1080,
        proxy_country_code="US",
    )
    assert warming._proxy_snapshot(account) == "socks5://1.2.3.4:1080 (US)"


@pytest.mark.asyncio
async def test_start_warming_blocks_not_ready_account() -> None:
    await create_account(AccountCreate(account_id="acc-1"))  # new, no proxy/channels
    with pytest.raises(warming.WarmingNotReadyError) as excinfo:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    assert excinfo.value.reasons
    assert "acc-1" not in warming._RUNTIME


@pytest.mark.asyncio
async def test_start_warming_ready_account_records_proxy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")

    card = await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    assert card.state == "active"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.proxy_snapshot is not None
    assert "1.2.3.4" in record.proxy_snapshot


@pytest.mark.asyncio
async def test_manual_start_clears_stale_next_run_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual Start must fire immediately, not honour an old persisted schedule."""

    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")
    far_future = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            cycles_completed=0,
            next_run_at=far_future,
        ),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.next_run_at is None


@pytest.mark.asyncio
async def test_load_board_attaches_readiness() -> None:
    await create_account(AccountCreate(account_id="acc-1"))  # not ready

    board = await warming.load_board()

    card = board.idle[0]
    assert card.readiness is not None
    assert card.readiness.ready is False


# --------------------------------------------------------------------------- #
# Quiet hours
# --------------------------------------------------------------------------- #


def test_in_quiet_hours_disabled_when_equal() -> None:
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 3, tzinfo=UTC), 5, 5) is False


def test_in_quiet_hours_non_wrapping_window() -> None:
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 2, tzinfo=UTC), 1, 5)
    assert not warming._in_quiet_hours(datetime(2026, 6, 12, 6, tzinfo=UTC), 1, 5)


def test_in_quiet_hours_wrapping_midnight() -> None:
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 23, tzinfo=UTC), 23, 7)
    assert warming._in_quiet_hours(datetime(2026, 6, 12, 2, tzinfo=UTC), 23, 7)
    assert not warming._in_quiet_hours(datetime(2026, 6, 12, 12, tzinfo=UTC), 23, 7)


def test_quiet_hours_end_at_rolls_to_next_day() -> None:
    end = warming._quiet_hours_end_at(datetime(2026, 6, 12, 23, 30, tzinfo=UTC), 7)
    assert end == datetime(2026, 6, 13, 7, 0, tzinfo=UTC)


def test_quiet_hours_end_at_same_day_when_ahead() -> None:
    end = warming._quiet_hours_end_at(datetime(2026, 6, 12, 2, 0, tzinfo=UTC), 7)
    assert end == datetime(2026, 6, 12, 7, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_run_loop_iteration_parks_during_quiet_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_loop, "_in_quiet_hours", lambda *_args: True)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        quiet_hours_enabled=True,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "quiet hours"
    assert recorder.actions == []  # no Telegram I/O during quiet hours
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.next_run_at is not None


@pytest.mark.asyncio
async def test_local_now_converts_to_account_timezone() -> None:
    await create_account(AccountCreate(account_id="acc-tz"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-tz",
            session_path="acc-tz",
            status="alive",
            is_temporary=False,
            phone="+12025550123",
        ),
    )
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    local = await warming._local_now("acc-tz", now)
    assert str(local.tzinfo) == "America/New_York"
    assert local.utcoffset() != now.utcoffset()


@pytest.mark.asyncio
async def test_local_now_falls_back_to_utc_without_phone() -> None:
    await create_account(AccountCreate(account_id="acc-nophone"))
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    assert await warming._local_now("acc-nophone", now) == now


# --------------------------------------------------------------------------- #
# Human pacing
# --------------------------------------------------------------------------- #


def test_human_delay_is_bounded_and_right_skewed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_seams, "rng", random.Random(7))  # noqa: S311 - deterministic test rng
    samples = [warming._human_delay(0.0, 10.0) for _ in range(2000)]
    assert all(0.0 <= sample <= 10.0 for sample in samples)
    # heavy right tail → most pauses sit below the midpoint, unlike a uniform draw
    below_midpoint = sum(1 for sample in samples if sample < 5.0)
    assert below_midpoint > len(samples) * 0.5
    # an equal range collapses to the value
    assert warming._human_delay(3.0, 3.0) == 3.0


def test_shift_to_active_hours_moves_night_into_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(night, None).hour == 8
    day = datetime(2026, 6, 12, 14, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(day, None) == day


def test_shift_to_active_hours_uses_account_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    # 03:00 UTC is the middle of the night in New York → shifted into the window.
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    shifted = warming._shift_to_active_hours(night, "America/New_York")
    local = shifted.astimezone(ZoneInfo("America/New_York"))
    assert 8 <= local.hour < 23


def test_shift_to_active_hours_bad_timezone_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(night, "Not/AZone").hour == 8


def test_shift_to_active_hours_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "active_hours_start", 0)
    monkeypatch.setattr(settings.warming, "active_hours_end", 0)
    night = datetime(2026, 6, 12, 3, 0, tzinfo=UTC)
    assert warming._shift_to_active_hours(night, None) == night


# --------------------------------------------------------------------------- #
# Daily counters
# --------------------------------------------------------------------------- #


def test_roll_daily_resets_on_new_day() -> None:
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=5,
        daily_count_date="2026-06-11",
    )
    assert warming._roll_daily(record, "2026-06-12") == (0, "2026-06-12")


def test_roll_daily_keeps_same_day() -> None:
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        daily_actions=5,
        daily_count_date="2026-06-12",
    )
    assert warming._roll_daily(record, "2026-06-12") == (5, "2026-06-12")


def test_roll_daily_handles_missing_record() -> None:
    assert warming._roll_daily(None, "2026-06-12") == (0, "2026-06-12")


@pytest.mark.asyncio
async def test_run_loop_iteration_parks_when_daily_cap_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        max_daily_actions=3,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=3,
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.next_run_at is not None


@pytest.mark.asyncio
async def test_run_loop_iteration_increments_daily_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_count_date == datetime.now(UTC).date().isoformat()
    # One channel per cycle: set_online + join + read = 3 attempts (set_offline does not count).
    assert record.daily_actions == 3


@pytest.mark.asyncio
async def test_cycle_hard_daily_limit_includes_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    # Give it only 2 remaining actions: SetOnline(True) uses 1, Join uses 1.
    # It should not attempt Read, but SetOnline(False) should still run.
    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=2)
    )

    assert result.attempted_actions == 2
    types = recorder.types()
    assert types == ["set_online", "join_channel", "set_online"]
    assert result.channels_joined == 1
    assert result.channels_read == 0


@pytest.mark.asyncio
async def test_cycle_hard_daily_limit_react_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.5)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=True,
        join_enabled=False,
        gemini_api_key="",
    )

    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=2)
    )

    assert result.attempted_actions == 2
    assert recorder.types() == ["set_online", "read_channel", "set_online"]
    assert result.channels_read == 1
    assert result.reactions_sent == 0


# --------------------------------------------------------------------------- #
# Disabled actions (join toggle)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cycle_skips_join_when_join_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        join_enabled=False,
        gemini_api_key="",
    )

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.channels_joined == 0
    assert "join_channel" not in recorder.types()
    assert "read_channel" in recorder.types()


@pytest.mark.asyncio
async def test_save_settings_persists_warming_controls() -> None:
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=True,
            enforce_readiness=False,
            quiet_hours_enabled=True,
            quiet_hours_start=23,
            quiet_hours_end=7,
            max_daily_actions=50,
        ),
    )

    assert masked.enforce_readiness is False
    assert masked.quiet_hours_enabled is True
    assert masked.quiet_hours_start == 23
    assert masked.quiet_hours_end == 7
    assert masked.max_daily_actions == 50

    reloaded = await warming.load_settings()
    assert reloaded.quiet_hours_start == 23
    assert reloaded.max_daily_actions == 50


# --------------------------------------------------------------------------- #
# next_run_at timing (restart respects the existing schedule)
# --------------------------------------------------------------------------- #


def test_seconds_until_future_and_past() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    future = (now + timedelta(seconds=120)).isoformat()
    assert warming._seconds_until(future, now) == pytest.approx(120)
    past = (now - timedelta(seconds=50)).isoformat()
    assert warming._seconds_until(past, now) == 0.0


def test_seconds_until_invalid_and_naive() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    assert warming._seconds_until("not-a-date", now) == 0.0
    # Naive timestamp is treated as UTC rather than crashing.
    assert warming._seconds_until("2026-06-12T00:01:00", now) == pytest.approx(60)


def test_initial_delay_respects_future_next_run() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        next_run_at=(now + timedelta(seconds=3600)).isoformat(),
    )
    assert warming._initial_delay_seconds(record, now) == pytest.approx(3600)


def test_initial_delay_uses_jitter_without_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "startup_jitter_max_seconds", 4.0)
    value = warming._initial_delay_seconds(None, datetime(2026, 6, 12, 0, 0, tzinfo=UTC))
    assert 0.0 <= value <= 4.0


def test_loop_sleep_respects_future_next_run() -> None:
    now = datetime(2026, 6, 12, 0, 0, tzinfo=UTC)
    record = WarmingStateRecord(
        account_id="a",
        state="sleeping",
        updated_at="t",
        next_run_at=(now + timedelta(seconds=900)).isoformat(),
    )
    assert warming._loop_sleep_seconds(record, now) == pytest.approx(900)


def test_loop_sleep_falls_back_without_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "cycle_sleep_min_hours", 1.0)
    monkeypatch.setattr(settings.warming, "cycle_sleep_max_hours", 1.0)
    value = warming._loop_sleep_seconds(None, datetime(2026, 6, 12, 0, 0, tzinfo=UTC))
    assert value == pytest.approx(3600)


@pytest.mark.asyncio
async def test_cycle_diagnostics_chat_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
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
async def test_start_warming_refreshes_pairs_before_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_refresh() -> None:
        calls.append("refresh")

    async def fake_loop(_account_id: str) -> None:
        calls.append("loop")

    monkeypatch.setattr("services.warming._runtime._refresh_dialogue_pairs", fake_refresh)
    monkeypatch.setattr("services.warming._runtime._warming_loop", fake_loop)
    from schemas.warming import WarmingReadiness  # noqa: PLC0415

    monkeypatch.setattr(
        "services.warming._runtime.evaluate_readiness",
        lambda *_a, **_kw: WarmingReadiness(ready=True, reasons=[]),
    )

    await create_account(AccountCreate(account_id="acc-a"))
    await _set_settings(chat=True, reactions=False, key="test")

    await warming.start_warming(StartWarmingRequest(account_id="acc-a"))

    # Verify order
    assert calls == ["refresh", "loop"]


@pytest.mark.asyncio
async def test_joined_channels_cleanup_on_channel_remove() -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from core.db import (  # noqa: PLC0415
        _get_engine,
        _warming_joined_channels,
        add_warming_channel,
        record_channel_joined,
    )
    from services.warming.channels import remove_warming_channel  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-a"))
    await add_warming_channel("testchan")
    await record_channel_joined("acc-a", "testchan")

    # Verify it exists
    with _get_engine().connect() as conn:
        res = conn.execute(select(_warming_joined_channels)).all()
        assert len(res) == 1

    # Remove channel
    await remove_warming_channel("testchan")

    # Verify cascade delete
    with _get_engine().connect() as conn:
        res = conn.execute(select(_warming_joined_channels)).all()
        assert len(res) == 0


@pytest.mark.asyncio
async def test_joined_channels_cleanup_on_account_delete() -> None:
    import asyncio  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415

    from core.db import (  # noqa: PLC0415
        _get_engine,
        _warming_joined_channels,
        add_warming_channel,
        record_channel_joined,
    )
    from core.repositories.accounts import _delete_account  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-a"))
    await add_warming_channel("testchan")
    await record_channel_joined("acc-a", "testchan")

    # Remove account
    await asyncio.to_thread(_delete_account, "acc-a")

    # Verify cascade delete
    with _get_engine().connect() as conn:
        res = conn.execute(select(_warming_joined_channels)).all()
        assert len(res) == 0


@pytest.mark.asyncio
async def test_delete_account_with_all_related_rows() -> None:
    """F4 regression: deleting a warmed account must not raise IntegrityError.

    Schema declares ForeignKey on account_proxies / warming_account_state /
    account_spam_status without ON DELETE CASCADE, so the repo has to clean
    children explicitly. We seed every per-account table that exists.
    """
    from core.db import (  # noqa: PLC0415
        _account_proxies,
        _account_spam_status,
        _accounts,
        _device_fingerprints,
        _get_engine,
        _warming_account_state,
        _warming_joined_channels,
        add_warming_channel,
        record_channel_joined,
        upsert_warming_state,
    )
    from core.repositories.accounts import _delete_account  # noqa: PLC0415
    from core.repositories.dialogues import (  # noqa: PLC0415
        dialogue_messages,
        dialogue_pairs,
        record_dialogue_message,
        replace_dialogue_pairs,
    )

    await create_account(AccountCreate(account_id="acc-a", session_name="acc-a"))
    await create_account(AccountCreate(account_id="acc-b", session_name="acc-b"))
    await add_warming_channel("testchan")
    await record_channel_joined("acc-a", "testchan")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-a", state="active"))
    await upsert_account_proxy(
        AccountProxyUpsert(
            account_id="acc-a",
            proxy_type="socks5",
            host="127.0.0.1",
            port=1080,
            username=None,
            password=None,
        ),
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-a",
            status="clean",
            detail=None,
            checked_at="2026-01-01T00:00:00+00:00",
        ),
    )
    await replace_dialogue_pairs([("acc-a", "acc-b")])
    await record_dialogue_message("acc-a", "acc-b", "hi")
    await record_dialogue_message("acc-b", "acc-a", "yo")

    await asyncio.to_thread(_delete_account, "acc-a")

    with _get_engine().connect() as conn:
        assert (
            conn.execute(sa_delete(_accounts).where(_accounts.c.account_id == "acc-a")).rowcount
            == 0
        )
        assert (
            conn.execute(
                _warming_joined_channels.select().where(
                    _warming_joined_channels.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _warming_account_state.select().where(
                    _warming_account_state.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _account_proxies.select().where(_account_proxies.c.account_id == "acc-a"),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _account_spam_status.select().where(
                    _account_spam_status.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                _device_fingerprints.select().where(
                    _device_fingerprints.c.account_id == "acc-a",
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                dialogue_messages.select().where(
                    (dialogue_messages.c.from_account == "acc-a")
                    | (dialogue_messages.c.to_account == "acc-a"),
                ),
            ).all()
            == []
        )
        assert (
            conn.execute(
                dialogue_pairs.select().where(
                    (dialogue_pairs.c.account_a == "acc-a")
                    | (dialogue_pairs.c.account_b == "acc-a"),
                ),
            ).all()
            == []
        )


@pytest.mark.asyncio
async def test_create_account_rejects_duplicate_session_name() -> None:
    """F5: two accounts cannot share one Telethon session file."""
    from core.repositories.accounts import DuplicateSessionNameError  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1", session_name="shared"))
    with pytest.raises(DuplicateSessionNameError):
        await create_account(AccountCreate(account_id="acc-2", session_name="shared"))


@pytest.mark.asyncio
async def test_create_account_allows_multiple_null_session_names() -> None:
    """F5: NULL session_name is not a value, so accounts without one can coexist."""
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    # No exception — both rows persist.


@pytest.mark.asyncio
async def test_stop_does_not_get_overwritten_by_inflight_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F1: a stop fired while ``run_one_cycle`` is in flight must stick."""
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    # Patch ``run_one_cycle`` to simulate stop_warming firing mid-cycle:
    # the operator wrote ``idle`` while the loop was still inside this call.
    async def cycle_with_stop_inside(req):  # type: ignore[no-untyped-def]
        await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))
        return WarmingCycleResult(account_id=req.account_id, status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", cycle_with_stop_inside)

    await run_loop_iteration("acc-1")
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"


@pytest.mark.asyncio
async def test_manual_start_replaces_existing_loop_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: re-starting an account must cancel a still-sleeping loop and create a fresh task."""
    started: list[str] = []
    cancelled = asyncio.Event()

    async def fake_loop(account_id: str) -> None:
        started.append(account_id)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    monkeypatch.setattr(settings.warming, "enforce_readiness", False)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    first_task = warming._RUNTIME["acc-1"]
    await asyncio.sleep(0)
    assert started == ["acc-1"]

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    second_task = warming._RUNTIME["acc-1"]
    await asyncio.sleep(0)

    assert second_task is not first_task
    assert cancelled.is_set()
    assert started == ["acc-1", "acc-1"]


@pytest.mark.asyncio
async def test_reconcile_skips_when_state_already_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F3: if state flipped to idle between listing and lock-acquire, do not restart."""
    from core.db import list_warming_states as real_list_states  # noqa: PLC0415

    started: list[str] = []

    async def fake_loop(account_id: str) -> None:
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    # Simulate the race: list_warming_states sees "active", then between that
    # and the per-account lock, stop_warming flips the row to "idle".
    async def race_list() -> list:  # type: ignore[type-arg]
        records = await real_list_states()
        await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))
        return records

    monkeypatch.setattr(_runtime, "list_warming_states", race_list)

    await warming.reconcile_warming_runtime()
    await asyncio.sleep(0)

    assert "acc-1" not in warming._RUNTIME
    assert started == []
