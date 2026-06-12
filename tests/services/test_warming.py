"""Tests for the warming engine (``services.warming``).

Telegram I/O (``execute``) and Gemini (``generate_text``) are patched at the
service boundary so the engine is exercised with no real network or sleeps.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

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
    save_warming_settings,
    update_account_from_session_check,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleRequest,
    WarmingSettingsUpdate,
    WarmingStateWrite,
)
from services import warming

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

    async def execute(self, account_id: str, action: TelegramAction) -> ActionResult:
        self.actions.append((account_id, action))
        status = "flood_wait" if action.action_type in self.flood_on else "ok"
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "skipped"
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_cycle_happy_path_joins_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    assert result.channels_joined == 0
    assert "read_channel" not in recorder.types()


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


@pytest.mark.asyncio
async def test_cycle_sends_inter_account_dm(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(warming, "execute", recorder.execute)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="hi there")

    monkeypatch.setattr(warming, "generate_text", fake_generate)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="error", error="quota")

    monkeypatch.setattr(warming, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_dm_without_peers(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
async def test_start_and_stop_warming_manage_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(warming, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))

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
    monkeypatch.setattr(warming, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    # Two warming accounts but the peer was never session-checked → user_id is None.
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-2", state="active"))

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


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
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
async def test_save_settings_clear_key_wipes_stored_value() -> None:
    await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            gemini_api_key="secret",
        ),
    )
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            clear_gemini_key=True,
        ),
    )
    assert masked.has_gemini_key is False


@pytest.mark.asyncio
async def test_save_settings_updates_gemini_model() -> None:
    masked = await warming.save_settings(
        WarmingSettingsUpdate(
            inter_account_chat=False,
            reactions_enabled=False,
            gemini_model="gemini-2.5-pro",
        ),
    )
    assert masked.gemini_model == "gemini-2.5-pro"


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

    monkeypatch.setattr(warming, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-1" in warming._RUNTIME
    # Give the loop a single scheduling tick so it actually starts.
    await asyncio.sleep(0)
    assert "acc-1" in started


@pytest.mark.asyncio
async def test_reconcile_marks_orphan_state_rows_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(warming, "_warming_loop", fake_loop)
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

    monkeypatch.setattr(warming, "_warming_loop", fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await create_account(AccountCreate(account_id="acc-2"))
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
