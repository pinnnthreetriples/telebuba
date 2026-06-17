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
    load_warming_settings,
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
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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
async def test_daily_limit_excludes_offline_cleanup(
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

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
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


async def _resolve(value):  # type: ignore[no-untyped-def]
    return value


async def fetch_account_helper(account_id: str):  # type: ignore[no-untyped-def]
    from core.db import fetch_account  # noqa: PLC0415

    return await fetch_account(account_id)


@pytest.mark.asyncio
async def test_try_reserve_sent_hash_concurrent_only_one_wins() -> None:
    """F7: parallel reservers of the same hash never both observe an empty window."""
    from core.db import purge_sent_hashes_older_than, try_reserve_sent_hash  # noqa: PLC0415

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
async def test_concurrent_set_state_increment_cycle_preserves_all_increments() -> None:
    """P2.4: N parallel _set_state(increment_cycle=True) → cycles_completed == N.

    The pre-fix code computed ``cycles + 1`` from a stale read in _set_state and
    handed the result to the upsert. Concurrent writers all read the same
    pre-state and clobbered each other (each thought their write was the next).
    The fix moves the bump into the ON CONFLICT DO UPDATE SQL expression.
    """
    from services.warming._state import _set_state  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    n_writers = 8

    async def bump(i: int) -> None:
        await _set_state("acc-1", "sleeping", last_event=f"cycle-{i}", increment_cycle=True)

    await asyncio.gather(*(bump(i) for i in range(n_writers)))
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.cycles_completed == n_writers


@pytest.mark.asyncio
async def test_migration_unique_session_name_handles_existing_duplicates(
    tmp_path: Path,
) -> None:
    """P1.3: migration #7 must auto-remediate legacy duplicates, not crash startup.

    Re-creates the pre-migration shape (no unique index) with two rows that
    share a session_name, then drives ``apply_migrations`` and asserts the
    index is in place and the second row's session_name was nulled.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415

    db_path = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        await _exercise_migration_seven(engine)
    finally:
        engine.dispose()


async def _exercise_migration_seven(engine) -> None:  # type: ignore[no-untyped-def]
    from core.migrations import apply_migrations  # noqa: PLC0415

    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE accounts ("
            "  account_id VARCHAR PRIMARY KEY,"
            "  label VARCHAR,"
            "  session_name VARCHAR,"
            "  status VARCHAR NOT NULL,"
            "  created_at VARCHAR NOT NULL,"
            "  updated_at VARCHAR NOT NULL"
            ")",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
            "VALUES ('acc-1', 'shared', 'new', '2026-01-01', '2026-01-01')",
        )
        connection.exec_driver_sql(
            "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
            "VALUES ('acc-2', 'shared', 'new', '2026-01-02', '2026-01-02')",
        )

    # Pretend the previous migrations already ran so #7 fires alone.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
            "applied_at VARCHAR NOT NULL)",
        )
        # Stamp every non-#7 migration as already applied so the test DB only
        # exercises the new index migration. Append the new version here when
        # adding a migration that touches a table this test does NOT create.
        already_applied = (1, 2, 3, 4, 5, 6, 8)
        for version in already_applied:
            connection.exec_driver_sql(
                "INSERT INTO schema_version VALUES (?, 'stub', '2026-01-01')",
                (version,),
            )

    # Must not raise.
    apply_migrations(engine)

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT account_id, session_name FROM accounts ORDER BY account_id",
        ).all()
        names = {str(row[0]): row[1] for row in rows}
        # Older row kept the name; the duplicate was nulled.
        assert names["acc-1"] == "shared"
        assert names["acc-2"] is None
        remediations = connection.exec_driver_sql(
            "SELECT account_id FROM schema_remediations WHERE migration = 7",
        ).all()
        assert [r[0] for r in remediations] == ["acc-2"]


@pytest.mark.asyncio
async def test_warming_loop_exits_when_state_becomes_idle_after_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1.1: a loop that survives stop_warming must exit on the next idle re-read.

    Simulates a runaway loop by patching ``run_loop_iteration`` to flip state to
    ``idle`` (the stop_warming effect) without actually cancelling the task.
    The loop must observe the idle row on the next fetch and break — *not*
    overwrite it with another cycle_started.
    """
    iterations: list[str] = []

    async def fake_iteration(
        account_id: str,
        *,
        run_id: str | None = None,  # noqa: ARG001
    ) -> WarmingCycleResult:
        iterations.append(account_id)
        await upsert_warming_state(WarmingStateWrite(account_id=account_id, state="idle"))
        return WarmingCycleResult(account_id=account_id, status="ok")

    monkeypatch.setattr(_runtime, "run_loop_iteration", fake_iteration)
    monkeypatch.setattr(_runtime, "_loop_sleep_seconds", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(_runtime, "_initial_delay_seconds", lambda *_args, **_kwargs: 0.0)

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    await _runtime._warming_loop("acc-1")

    assert iterations == ["acc-1"]
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"


@pytest.mark.asyncio
async def test_run_loop_iteration_bails_when_state_already_idle() -> None:
    """P1.1: a stale iteration started for an already-stopped account is a no-op."""
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="idle"))

    result = await run_loop_iteration("acc-1")
    assert result.status == "skipped"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The early-exit did NOT overwrite ``idle`` with ``cycle_started``.
    assert state.state == "idle"


@pytest.mark.asyncio
async def test_old_cycle_cannot_overwrite_new_manual_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P1.2: an in-flight cycle from a previous start must not write through.

    Simulates the race: cycle A is running ``run_one_cycle`` (state=active,
    run_id=A); meanwhile a second start_warming has flipped run_id → B and
    written 'queued' for the new generation. When A's iteration tries to write
    its final next_state, the run_id mismatch must turn the write into a no-op.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    # Stage the DB: run_id_b is the "new" generation; the old cycle holds run_id_a.
    run_id_a = "old-run"
    run_id_b = "new-run"
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id=run_id_a),
    )

    async def cycle_with_restart_inside(req):  # type: ignore[no-untyped-def]
        # Simulate start_warming firing during this in-flight cycle: it minted
        # a fresh run_id and wrote it (along with state='active') to the row.
        await upsert_warming_state(
            WarmingStateWrite(
                account_id=req.account_id,
                state="active",
                run_id=run_id_b,
                last_event="queued",
            ),
        )
        return WarmingCycleResult(account_id=req.account_id, status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", cycle_with_restart_inside)

    await run_loop_iteration("acc-1", run_id=run_id_a)
    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The new generation owns the row; the stale cycle's final write must not
    # have flipped state to 'sleeping'/'error' or rolled run_id back to A.
    assert state.run_id == run_id_b
    assert state.state == "active"
    assert state.last_event == "queued"


@pytest.mark.asyncio
async def test_start_warming_mints_fresh_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """P1.2: each manual start writes a new run_id distinct from the previous one."""

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    state_first = await fetch_warming_state("acc-1")
    assert state_first is not None
    first_run_id = state_first.run_id
    assert first_run_id is not None

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    state_second = await fetch_warming_state("acc-1")
    assert state_second is not None
    assert state_second.run_id is not None
    assert state_second.run_id != first_run_id


@pytest.mark.asyncio
async def test_concurrent_create_duplicate_session_name_raises_domain_error() -> None:
    """P2.5: a duplicate-session_name race must raise a typed domain error.

    When two creates race on the same session_name, the loser gets
    DuplicateSessionNameError (typed domain error), never RuntimeError.

    Two parallel create_account calls with the same session_name go through
    asyncio.to_thread (separate connections). One wins the cooperative
    pre-check + INSERT; the other's INSERT trips migration #7's unique index.
    The post-IntegrityError branch in ``_create_account`` must translate that
    into a DuplicateSessionNameError, not pass through to the catch-all
    "Account was not persisted" RuntimeError.
    """
    from core.repositories.accounts import DuplicateSessionNameError  # noqa: PLC0415

    results = await asyncio.gather(
        create_account(AccountCreate(account_id="acc-1", session_name="shared")),
        create_account(AccountCreate(account_id="acc-2", session_name="shared")),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DuplicateSessionNameError)


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
async def test_remove_account_stops_runtime_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """P3.7: removing an active warming account must stop its runtime task.

    Repo-level _delete_account is layer-correct in not touching _RUNTIME; the
    service-level ``remove_account`` is what callers should use to avoid leaving
    an orphan task that keeps trying to act on a vanished account.
    """
    from services.accounts.lifecycle import remove_account  # noqa: PLC0415

    started_events: list[str] = []
    cancelled_events: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started_events.append(account_id)
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled_events.append(account_id)
            raise

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))
    await asyncio.sleep(0)
    assert "acc-1" in warming._RUNTIME

    await remove_account("acc-1")

    assert "acc-1" not in warming._RUNTIME
    assert cancelled_events == ["acc-1"]
    # DB row gone too.
    from core.db import fetch_account  # noqa: PLC0415

    assert await fetch_account("acc-1") is None
