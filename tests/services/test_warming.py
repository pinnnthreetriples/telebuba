"""Tests for the warming engine (``services.warming``).

Telegram I/O (``execute``) and Gemini (``generate_text``) are patched at the
service boundary so the engine is exercised with no real network or sleeps.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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
    update_account_proxy_check,
    upsert_account_proxy,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate, AccountRead
from schemas.gemini import GeminiResult
from schemas.proxy import AccountProxyCheckUpdate, AccountProxyUpsert
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingCycleRequest,
    WarmingSettingsUpdate,
    WarmingStateRecord,
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.messages_sent == 0
    assert "send_dm" not in recorder.types()


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
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

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
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

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
async def test_start_and_stop_warming_manage_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_loop(_account_id: str) -> None:
        await asyncio.sleep(3600)

    monkeypatch.setattr(warming, "_warming_loop", fake_loop)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
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

    monkeypatch.setattr(warming, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")

    card = await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    assert card.state == "active"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.proxy_snapshot is not None
    assert "1.2.3.4" in record.proxy_snapshot


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
    monkeypatch.setattr(warming, "_in_quiet_hours", lambda *_args: True)
    recorder = _Recorder()
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
    monkeypatch.setattr(warming, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_count_date == datetime.now(UTC).date().isoformat()
    # One channel per cycle: join + read = 2 actions.
    assert record.daily_actions == 2


# --------------------------------------------------------------------------- #
# Disabled actions (join toggle)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cycle_skips_join_when_join_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(warming, "execute", recorder.execute)
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
