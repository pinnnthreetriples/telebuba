"""Tests for the warming engine (``services.warming``).

Telegram I/O (``execute``) and Gemini (``generate_text``) are patched at the
service boundary so the engine is exercised with no real network or sleeps.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
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
    update_proxy_check,
    upsert_spam_status,
    upsert_warming_state,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate, AccountRead
from schemas.gemini import GeminiResult
from schemas.proxy import ProxyCheckUpdate
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.telegram_session import TelegramSessionCheckResult
from schemas.trust import TrustScore
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
    WarmingStateWriteResult,
)
from services import warming
from services.content import register_sent
from services.dialogues import assign_pairs
from services.warming import _loop, _runner, _runtime, _seams, _transitions
from services.warming.pacing import (
    _seconds_until,
    persona_dm_probability,
    persona_reaction_probability,
)
from tests.factories import seed_account_proxy

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
    # Deterministic probability rolls: the reaction + persona-DM gates always
    # "pass" so tests exercise the real gates (reactions_enabled / dm_ok /
    # pending / cap), not the RNG. Tests that need a roll to *fail* override this.
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)
    reset_logging_for_tests()
    setup_logging()
    warming._RUNTIME.clear()
    # _ACCOUNT_LOCKS are module-level and bound to the loop alive when created;
    # clear them too so each test gets fresh locks — needed when a runner like
    # mutmut drives several pytest sessions in one process (the loop changes).
    warming._ACCOUNT_LOCKS.clear()
    yield
    warming._RUNTIME.clear()
    warming._ACCOUNT_LOCKS.clear()
    # Abandon the periodic purge task (reconcile starts one) like the per-account
    # loops above, so it doesn't leak across tests.
    if _runtime._PURGE_TASK is not None:
        _runtime._PURGE_TASK.cancel()
        _runtime._PURGE_TASK = None
    reset_logging_for_tests()


async def _seed_channel() -> None:
    await warming.add_channels(AddChannelsRequest(raw="@channel_one"))


async def _set_settings(
    *, chat: bool, reactions: bool, key: str | None, enforce_readiness: bool = True
) -> None:
    await save_warming_settings(
        inter_account_chat=chat,
        reactions_enabled=reactions,
        enforce_readiness=enforce_readiness,
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
async def test_cycle_emits_progress_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)  # always react (persona roll passes)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    steps: list[str] = []

    async def _record(step: str) -> None:
        steps.append(step)

    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"), on_step=_record)

    assert steps == ["set_online", "join", "read", "react"]


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


@pytest.mark.asyncio
async def test_cycle_watches_peer_stories(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every persona glances at a subscribed peer's stories once per session.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "ok"
    assert "watch_peer_stories" in recorder.types()


@pytest.mark.asyncio
async def test_cycle_skips_story_view_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert "watch_peer_stories" not in recorder.types()


@pytest.mark.asyncio
async def test_cycle_story_view_peer_flood_stops_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.peer_flood_on.add("watch_peer_stories")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert "watch_peer_stories" in recorder.types()
    assert result.status == "peer_flood"
    assert result.last_failed_action == "watch_peer_stories"


@pytest.mark.asyncio
async def test_cycle_story_view_flood_wait_stops_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    recorder.flood_on.add("watch_peer_stories")
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    assert result.last_failed_action == "watch_peer_stories"


# --------------------------------------------------------------------------- #
# Intensity ceiling (phase + trust) and persona presets
# --------------------------------------------------------------------------- #


def _configure_intensity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 1)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 3)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 36.0)


def test_compute_intensity_ceiling_for_fresh_account(monkeypatch: pytest.MonkeyPatch) -> None:
    # The age-ramp is retired: channel range is the flat config, and a fresh
    # account is throttled by the phase cap + the DM cold-start guard, not by a
    # per-cycle ramp. Intro phase, cap 3, DM blocked under dm_min_age.
    _configure_intensity(monkeypatch)
    fresh = warming.compute_intensity(0.0)
    assert fresh.channels_max == 3
    assert fresh.phase == "intro"
    assert fresh.daily_cap == 3
    assert fresh.dm_allowed is False


def test_compute_intensity_dm_unlocks_at_min_age(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_intensity(monkeypatch)
    # DM unlocks exactly at the cold-start threshold; channel range stays flat.
    assert warming.compute_intensity(36.0).dm_allowed is True
    assert warming.compute_intensity(35.0).dm_allowed is False
    assert warming.compute_intensity(500.0).channels_max == 3


def test_compute_intensity_dm_gated_by_trust_band(monkeypatch: pytest.MonkeyPatch) -> None:
    # DM permission depends on trust band, not age alone (audit П11): only
    # excellent/good/watch may DM; at_risk/critical may not.
    _configure_intensity(monkeypatch)
    assert warming.compute_intensity(500.0, trust_band="good").dm_allowed is True
    assert warming.compute_intensity(500.0, trust_band="watch").dm_allowed is True
    assert warming.compute_intensity(500.0, trust_band="at_risk").dm_allowed is False
    assert warming.compute_intensity(500.0, trust_band="critical").dm_allowed is False
    # A healthy band cannot un-block DM for a too-young account.
    assert warming.compute_intensity(0.0, trust_band="good").dm_allowed is False
    # No band passed → age-only, so direct callers (run_one_cycle) are unchanged.
    assert warming.compute_intensity(500.0).dm_allowed is True


def test_persona_presets_scale_reaction_and_dm() -> None:
    # Persona sets per-session frequency; calm < normal < active for both levers.
    assert (
        persona_reaction_probability("calm")
        < persona_reaction_probability("normal")
        < persona_reaction_probability("active")
    )
    assert (
        persona_dm_probability("calm")
        < persona_dm_probability("normal")
        < persona_dm_probability("active")
    )


def test_persona_next_run_seconds_capped_by_phase_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # A tiny daily cap (young account) forces one long gap regardless of the
    # persona's headline sessions/day — the phase ceiling throttles cadence.
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)
    monkeypatch.setattr(settings.warming, "next_run_jitter_fraction", 0.0)
    rng = random.Random(1)  # noqa: S311 - deterministic test rng
    # intro cap 3 affords 0 sessions → floored to 1 → gap == the full 15-h window.
    gap = warming.persona_next_run_seconds("active", 3, rng)
    assert gap == pytest.approx(15 * 3600)


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
async def test_loop_iteration_persists_live_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    active_actions: list[str | None] = []
    real_set_state = _loop._set_state

    async def _spy(account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        if state == "active":
            active_actions.append(cast("str | None", kwargs.get("last_action")))
        return await real_set_state(account_id, state, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_loop, "_set_state", _spy)

    await warming.run_loop_iteration("acc-1")

    # cycle_started seeds set_online; the monotonic hook advances the rail forward
    # only (no backward bounce across the per-channel join/read/react). The exact
    # tail depends on the daily-action cap, so assert a forward-only prefix that
    # has at least reached the channel-read step.
    assert active_actions == list(_loop._PROGRESS_STEPS[: len(active_actions)])
    assert "read" in active_actions


@pytest.mark.asyncio
async def test_loop_iteration_survives_progress_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A raising progress write (e.g. a transient SQLite lock) must not abort the
    # cycle or park a healthy account in error — the hook is cosmetic, best-effort.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    real_set_state = _loop._set_state

    async def _flaky(account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        # Only the mid-cycle progress writes blow up (state=active, no last_event);
        # the cycle_started / finalize boundary writes go through untouched.
        if state == "active" and kwargs.get("last_event") is None:
            msg = "database is locked"
            raise RuntimeError(msg)
        return await real_set_state(account_id, state, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_loop, "_set_state", _flaky)

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state != "error"


@pytest.mark.asyncio
async def test_loop_iteration_clears_stale_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    # The rail advances last_action live but never updates last_channel, so a
    # prior cycle's failed channel must be cleared at cycle start, not left to
    # surface stale under a fresh active step.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    # enforce_readiness off: this test is about stale-channel clearing, not the
    # П3 readiness gate (the bare account would otherwise be parked).
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", last_channel="old-chan"),
    )

    active_channels: list[str | None] = []
    real_set_state = _loop._set_state

    async def _spy(account_id: str, state: str, **kwargs: object) -> WarmingStateWriteResult:
        write = await real_set_state(account_id, state, **kwargs)  # ty: ignore[invalid-argument-type]
        if state == "active":
            active_channels.append(write.record.last_channel)
        return write

    monkeypatch.setattr(_loop, "_set_state", _spy)

    await warming.run_loop_iteration("acc-1")

    assert active_channels  # the cycle reached at least the cycle_started write
    assert "old-chan" not in active_channels


@pytest.mark.asyncio
async def test_no_channels_cycle_does_not_increment_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cycle that finds no channels is a no-op skip — it must not bump
    # cycles_completed (false progress) (audit П9). enforce_readiness is off so
    # the loop reaches the cycle's skip path rather than the П3 readiness gate.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.cycles_completed == 0
    assert record.state == "sleeping"


@pytest.mark.asyncio
async def test_run_loop_iteration_parks_degraded_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A running account that degrades mid-warming (spam-limited here) must be
    # parked to error when enforce_readiness is on — not warmed on while the
    # card already shows the blocker (audit П3).
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="acc-1",
            status="limited",
            detail="restricted until 2026-12-31",
            checked_at="2026-06-22T00:00:00+00:00",
        ),
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "error"
    assert recorder.actions == []  # no cycle ran
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "error"
    assert "spam" in (record.last_error or "")


@pytest.mark.asyncio
async def test_run_loop_iteration_runs_ready_account_under_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The gate is a guard, not a blanket block: a still-ready account cycles
    # normally with enforce_readiness on (audit П3).
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="sleeping"))

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    assert "set_online" in recorder.types()


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


async def _fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_start_warming_persists_chosen_target_days(monkeypatch: pytest.MonkeyPatch) -> None:
    # The day slider's value reaches the warming-state row (was silently dropped).
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1", target_days=5))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 5


@pytest.mark.asyncio
async def test_start_warming_defaults_target_to_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # Omitting target_days falls back to the configured warmed_min_days floor.
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    monkeypatch.setattr(settings.neurocomment, "warmed_min_days", 9)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 9


@pytest.mark.asyncio
async def test_restart_while_warming_keeps_original_target_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A restart while the account is still warming must keep the ORIGINAL pick
    # (mirrors the persona rule). Honouring a smaller target here would complete
    # a still-anchored account on its next iteration.
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="sleeping", started_at=old_start, target_days=14
        ),
    )

    # Operator restarts with a *different* target while it is still warming.
    await warming.start_warming(StartWarmingRequest(account_id="acc-1", target_days=3))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 14  # original kept, not the new 3


@pytest.mark.asyncio
async def test_genuine_restart_honours_new_target_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A genuine (re)start from idle honours the new value (started_at is re-stamped).
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="idle",
            started_at=old_start,
            stopped_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
            target_days=14,
        ),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1", target_days=3))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.target_days == 3  # new value honoured on a genuine restart


@pytest.mark.asyncio
async def test_loop_auto_completes_at_target_days(monkeypatch: pytest.MonkeyPatch) -> None:
    # Once warming has run for the operator-chosen target, the loop parks the
    # account complete (no further cycle) instead of warming on indefinitely.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="sleeping", started_at=old_start, target_days=3
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "target reached"
    assert recorder.actions == []  # no cycle ran
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"
    assert record.last_event == "warming_complete"


@pytest.mark.asyncio
async def test_loop_keeps_warming_before_target(monkeypatch: pytest.MonkeyPatch) -> None:
    # Below the chosen target the account keeps cycling normally.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    recent_start = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1", state="sleeping", started_at=recent_start, target_days=7
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "ok"
    assert "set_online" in recorder.types()


@pytest.mark.asyncio
async def test_loop_target_complete_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    # A row already flagged complete re-parks silently — no second cycle, no re-log.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=old_start,
            target_days=3,
            last_event="warming_complete",
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "target reached"
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_target_gate_bails_on_stale_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # A concurrent stop/restart flips run_id, so the CAS write is rejected and the
    # gate bails as "stale run" rather than logging a phantom completion.
    record = WarmingStateRecord(
        account_id="acc-1",
        state="sleeping",
        updated_at="2026-06-01T00:00:00+00:00",
        started_at=(datetime.now(UTC) - timedelta(days=10)).isoformat(),
        target_days=3,
    )

    async def _rejected(*_args: object, **_kwargs: object) -> WarmingStateWriteResult:
        return WarmingStateWriteResult(record=record, applied=False)

    monkeypatch.setattr(_transitions, "_set_state", _rejected)

    result = await _transitions._gate_target_reached(
        "acc-1", record, datetime.now(UTC), run_id="gen-1"
    )

    assert result is not None
    assert result.detail == "stale run"


@pytest.mark.asyncio
async def test_target_complete_reparks_future_next_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: the idempotent target-reached branch must rewrite a fresh future
    # ``next_run_at``. Without it, once the first parked midnight passes the loop
    # busy-spins (``_loop_sleep_seconds`` clamps a past time to 0 and sleeps 0s).
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_ready_account("acc-1")
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    old_start = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    # Seed a row already flagged complete, with a next_run_at in the PAST (the
    # midnight the first pass parked has since elapsed) — the busy-spin trigger.
    stale_next = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            started_at=old_start,
            target_days=3,
            last_event="warming_complete",
            next_run_at=stale_next,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "target reached"
    assert recorder.actions == []  # still no cycle work
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.next_run_at is not None
    # The re-parked schedule must be in the future, so the loop sleeps a positive
    # interval instead of tight-spinning with asyncio.sleep(0).
    assert _seconds_until(record.next_run_at, datetime.now(UTC)) > 0.0


@pytest.mark.asyncio
async def test_load_board_card_exposes_target_days() -> None:
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            started_at="2026-06-01T00:00:00+00:00",
            target_days=10,
        ),
    )

    board = await warming.load_board()

    card = next(c for c in board.warming if c.account_id == "acc-1")
    assert card.target_days == 10


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
    # Isolate the restart mechanism; the readiness gate on reconcile is covered
    # by test_reconcile_parks_unready_account_when_enforced (#99).
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
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
        "services.warming._purge.purge_logs_older_than",
        await make_recorder("logs"),
    )
    monkeypatch.setattr(
        "services.warming._purge.purge_dialogue_messages_older_than",
        await make_recorder("dialogues"),
    )
    monkeypatch.setattr(
        "services.warming._purge.purge_sent_hashes_older_than",
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
async def test_periodic_purge_task_reruns_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    # The background sweep must rerun retention on its interval, not only at
    # startup — otherwise the append-only tables grow unbounded during uptime.
    fired = asyncio.Event()

    async def fake_purge() -> None:
        fired.set()

    monkeypatch.setattr(_runtime, "purge_stale_history", fake_purge)
    # Tiny interval so the first sleep elapses immediately.
    monkeypatch.setattr(settings.warming, "purge_interval_hours", 0.0000001)

    _runtime._start_purge_task()
    try:
        await asyncio.wait_for(fired.wait(), timeout=1.0)
    finally:
        await _runtime._stop_purge_task()

    assert fired.is_set()


@pytest.mark.asyncio
async def test_shutdown_cancels_periodic_purge_task(monkeypatch: pytest.MonkeyPatch) -> None:
    # The purge task is cancelled and awaited cleanly on shutdown (no leak).
    async def fake_purge() -> None:
        return None

    monkeypatch.setattr(_runtime, "purge_stale_history", fake_purge)
    _runtime._start_purge_task()
    task = _runtime._PURGE_TASK
    assert task is not None
    assert not task.done()

    await warming.shutdown_warming_runtime()

    assert _runtime._PURGE_TASK is None
    assert task.cancelled()


@pytest.mark.asyncio
async def test_reconcile_starts_periodic_purge_task(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reconcile (the lifespan entrypoint) must spin up the background sweep.
    async def fake_purge() -> None:
        return None

    monkeypatch.setattr(_runtime, "purge_stale_history", fake_purge)

    await warming.reconcile_warming_runtime()

    assert _runtime._PURGE_TASK is not None
    assert not _runtime._PURGE_TASK.done()


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
    proxy_id = await seed_account_proxy(account_id, host="1.2.3.4")
    await update_proxy_check(
        ProxyCheckUpdate(
            proxy_id=proxy_id,
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
async def test_start_warming_clears_stale_action_and_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Right after Start the card must not show the previous run's action/channel
    # (audit П6); the cycle hasn't begun yet, so they must be cleared at queue.
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            last_action="send_dm",
            last_channel="old-chan",
        ),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.last_event == "queued"
    assert record.last_action is None
    assert record.last_channel is None


@pytest.mark.asyncio
async def test_start_warming_preserves_started_at_on_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Restarting an already-warming account keeps the original stint anchor so
    # "дней в прогреве" counts from the first start, not this restart (audit П7).
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")
    original = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="sleeping", started_at=original),
    )

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.started_at == original


@pytest.mark.asyncio
async def test_start_warming_from_idle_stamps_started_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A genuine start (no prior warming row) stamps a fresh anchor.
    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await _seed_ready_account("acc-1")

    await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.started_at is not None


@pytest.mark.asyncio
async def test_load_board_attaches_readiness() -> None:
    await create_account(AccountCreate(account_id="acc-1"))  # not ready

    board = await warming.load_board()

    card = board.idle[0]
    assert card.readiness is not None
    assert card.readiness.ready is False


@pytest.mark.asyncio
async def test_load_board_dm_chip_mirrors_engine_readiness_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The DM chip must match what the engine does: readiness gates DM only when
    # enforce_readiness is on. With it off the engine skips the readiness gate,
    # so a not-ready but DM-eligible account must still show DM allowed (review).
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    monkeypatch.setattr(
        "services.warming.board.account_trust_score_from",
        lambda **_: TrustScore(account_id="acc-1", score=90, band="good"),
    )
    await create_account(AccountCreate(account_id="acc-1"))  # no proxy/session/channels → not ready

    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    assert (await warming.load_board()).idle[0].dm_allowed is True

    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=True)
    assert (await warming.load_board()).idle[0].dm_allowed is False


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
    # A fresh account is intro-capped at 3 by the auto cap (П2 retired the
    # fleet-wide override); enforce_readiness off so the daily gate is the one
    # that fires, not the П3 readiness gate.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
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
async def test_legacy_max_daily_override_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A legacy fleet-wide max_daily_actions persisted in the DB must NOT override
    # the per-account auto cap from phase/trust (audit П2). A fresh account is
    # intro-capped at 3, so daily_actions=3 parks despite the 999 override.
    # enforce_readiness off so the daily gate is reached, not the П3 readiness gate.
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        max_daily_actions=999,
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
            max_daily_actions=50,
        ),
    )

    assert masked.enforce_readiness is False
    assert masked.max_daily_actions == 50

    reloaded = await warming.load_settings()
    assert reloaded.enforce_readiness is False
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
    # No persisted schedule (shouldn't happen after run_loop_iteration writes one)
    # → a persona-paced gap, not a crash. Assert it's a sane positive duration.
    monkeypatch.setattr(settings.warming, "next_run_jitter_fraction", 0.0)
    value = warming._loop_sleep_seconds(None, datetime(2026, 6, 12, 0, 0, tzinfo=UTC))
    assert 0.0 < value <= 24 * 3600


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

    Schema declares ForeignKey on warming_account_state / account_spam_status
    without ON DELETE CASCADE, so the repo has to clean children explicitly. We
    seed every per-account table that exists. The shared pool proxy is NOT a
    child (accounts.proxy_id → proxies.id) and must survive the deletion.
    """
    from core.db import (  # noqa: PLC0415
        _account_spam_status,
        _accounts,
        _device_fingerprints,
        _get_engine,
        _warming_account_state,
        _warming_joined_channels,
        add_warming_channel,
        list_proxies,
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
    await seed_account_proxy("acc-a")
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

    # The shared pool proxy is not a child — it must outlive the deleted account.
    assert len((await list_proxies()).proxies) == 1


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
    # enforce_readiness off: this is a stop/CAS race test, not the П3 gate.
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    # Patch ``run_one_cycle`` to simulate stop_warming firing mid-cycle:
    # the operator wrote ``idle`` while the loop was still inside this call.
    async def cycle_with_stop_inside(req, **_kwargs):  # type: ignore[no-untyped-def]
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
        already_applied = (1, 2, 3, 4, 5, 6, 8, 10, 16)
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

    monkeypatch.setattr(_runner, "run_loop_iteration", fake_iteration)
    monkeypatch.setattr(_runner, "_loop_sleep_seconds", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(_runner, "_initial_delay_seconds", lambda *_args, **_kwargs: 0.0)

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-1", state="active"))

    await _runner._warming_loop("acc-1")

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
    # enforce_readiness off: this is a run_id/CAS race test, not the П3 gate.
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)

    # Stage the DB: run_id_b is the "new" generation; the old cycle holds run_id_a.
    run_id_a = "old-run"
    run_id_b = "new-run"
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id=run_id_a),
    )

    async def cycle_with_restart_inside(req, **_kwargs):  # type: ignore[no-untyped-def]
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
async def test_create_account_post_integrity_branch_raises_domain_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2.5: post-IntegrityError branch raises DuplicateSessionNameError.

    The IntegrityError-on-INSERT branch translates the unique-index violation
    into DuplicateSessionNameError, not RuntimeError.

    Drives the post-IntegrityError path deterministically by patching the
    cooperative pre-check SELECT to return None, so the INSERT actually fires
    and the migration-#7 unique index raises IntegrityError. The fix must
    re-read after the IntegrityError, find the conflict, and surface the
    typed domain error — never the catch-all "Account was not persisted".

    A live asyncio.gather race would be the ideal regression test, but
    SQLite + WAL + busy_timeout produces non-deterministic OperationalError
    ('database is locked') under thread contention, so we stick to the
    deterministic unit shape.
    """
    from core.repositories import accounts as accounts_repo  # noqa: PLC0415
    from core.repositories.accounts import DuplicateSessionNameError  # noqa: PLC0415

    # Plant the conflicting row first.
    await create_account(AccountCreate(account_id="acc-1", session_name="shared"))

    # Patch the pre-check SELECT to always return None so the second create
    # proceeds straight to INSERT and trips the unique index.
    original_create = accounts_repo._create_account

    def patched_create(data):  # type: ignore[no-untyped-def]
        # Temporarily blind the pre-check by patching select() inside the
        # accounts module to return a SELECT that yields no rows on .first().
        original_select = accounts_repo.select
        call_count = {"n": 0}

        class _NullFirst:
            def first(self) -> None:
                return None

        def _select_returning_null(*args, **kwargs):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] == 1:
                # The first select() call inside _create_account is the
                # pre-check. Return a SELECT that genuinely won't match the
                # conflict so the INSERT path runs.
                return original_select(
                    accounts_repo._accounts.c.account_id,
                ).where(accounts_repo._accounts.c.account_id == "__no_such_id__")
            return original_select(*args, **kwargs)

        monkeypatch.setattr(accounts_repo, "select", _select_returning_null)
        try:
            return original_create(data)
        finally:
            monkeypatch.setattr(accounts_repo, "select", original_select)

    monkeypatch.setattr(accounts_repo, "_create_account", patched_create)

    with pytest.raises(DuplicateSessionNameError):
        await create_account(AccountCreate(account_id="acc-2", session_name="shared"))


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


@pytest.mark.asyncio
async def test_real_stop_clears_run_id_so_stale_final_write_cannot_resurrect_idle() -> None:
    """Round-4 P1.1: drives the *real* _stop_warming_locked + a stale CAS write.

    Earlier round 3 test simulated stop with a hand-written upsert that
    cleared run_id — that masked a live bug where _stop_warming_locked did
    NOT clear run_id, so a stale loop's CAS write (run_id still matches)
    could sneak past and overwrite ``idle`` with ``sleeping``. This test
    invokes the real stop helper and asserts both legs of the fix:
    (1) stop clears run_id, (2) even if it did not, the upsert's CAS
    rejects any UPDATE that would overwrite an idle row.
    """
    from services.warming._runtime import _stop_warming_locked  # noqa: PLC0415
    from services.warming._state import _set_state  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="run-a"),
    )

    # Real stop. Must clear run_id (belt) so any stale CAS using run-a misses.
    await _stop_warming_locked("acc-1")
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"
    assert state.run_id is None

    # Now manually re-stamp run_id to simulate a future regression where
    # stop forgot to clear it. The CAS-rejects-idle suspenders must still
    # protect the row from a stale loop's write.
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="idle", run_id="run-a"),
    )
    await _set_state(
        "acc-1",
        "sleeping",
        last_event="cycle:ok",
        expected_run_id="run-a",
    )
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "idle"  # suspenders held — the stale write was a no-op
    assert state.last_event != "cycle:ok"


@pytest.mark.asyncio
async def test_restart_between_run_id_check_and_cycle_started_write_loses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-2 P1: a stale iteration cannot stamp 'cycle_started' on top of a new run.

    Forces the race by patching ``fetch_warming_state`` so the first call (the
    iteration's _matches_active_run guard) sees the OLD run_id, but the row in
    the DB has already been advanced to a fresh run_id by a new start_warming.
    The CAS clause on the cycle_started upsert must then refuse to mutate the
    row — the new generation's state must survive untouched.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    # DB row is on the NEW generation already.
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            last_event="queued",
            run_id="run-b",
        ),
    )

    # The stale guard sees a stale snapshot (run_id=run-a). The CAS on the
    # subsequent _set_state must catch the mismatch and skip the UPDATE.
    real_fetch = _loop.fetch_warming_state

    fetch_calls = {"n": 0}

    async def fake_fetch(account_id: str):  # type: ignore[no-untyped-def]
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            # First fetch is the guard; lie about run_id so the guard accepts.
            real = await real_fetch(account_id)
            if real is None:
                return real
            return real.model_copy(update={"run_id": "run-a"})
        return await real_fetch(account_id)

    monkeypatch.setattr(_loop, "fetch_warming_state", fake_fetch)

    # Stub the cycle so we don't reach real Telethon — the CAS we're testing
    # fires on cycle_started *before* the cycle runs, so the stub's content
    # doesn't matter for the assertion.
    async def stub_cycle(req):  # type: ignore[no-untyped-def]
        return WarmingCycleResult(account_id=req.account_id, status="ok")

    monkeypatch.setattr(_loop, "run_one_cycle", stub_cycle)

    await run_loop_iteration("acc-1", run_id="run-a")
    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The stale cycle_started write was a no-op; new generation's row stands.
    assert state.run_id == "run-b"
    assert state.last_event == "queued"


@pytest.mark.asyncio
async def test_run_loop_iteration_bails_when_state_error() -> None:
    """Round-2 P2.3: direct call on error account must not resurrect a cycle."""
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="error", last_error="boom"),
    )

    result = await run_loop_iteration("acc-1")
    assert result.status == "skipped"
    state = await fetch_warming_state("acc-1")
    assert state is not None
    assert state.state == "error"
    assert state.last_error == "boom"


@pytest.mark.asyncio
async def test_remove_account_blocks_concurrent_start_until_delete_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-2 P2.2: remove_account holds the lifecycle lock across stop + delete.

    Forces the race shape: a parallel ``start_warming`` is dispatched while
    ``remove_account`` is mid-flight. With the lock held across both stop and
    delete, the start has to wait until delete finishes; by then the account
    is gone, so start raises UnknownAccountError and no orphan task is
    created. Without the lock, the start would interleave and produce an
    orphan task pointing at a deleted account.
    """
    from services.accounts.lifecycle import remove_account  # noqa: PLC0415

    started_events: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started_events.append(account_id)
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
    await asyncio.sleep(0)
    started_events.clear()  # drop the legitimate first start

    # Run remove and a concurrent start. If the lock isn't held, the start
    # races into _RUNTIME before delete_account; if it is, start waits for
    # the lock, finds the account gone, and bails with UnknownAccountError.
    remove_task = asyncio.create_task(remove_account("acc-1"))
    await asyncio.sleep(0)  # give remove a chance to take the lock

    with pytest.raises(warming.UnknownAccountError):
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    await remove_task

    # No orphan task survived the race.
    assert "acc-1" not in warming._RUNTIME
    assert started_events == []


@pytest.mark.asyncio
async def test_stale_cycle_started_cas_failure_prevents_telegram_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-4 P1.2: a CAS no-op on cycle_started must abort run_one_cycle.

    Forces the race: the iteration's initial _matches_active_run guard accepts
    the stale run_id (we lie via fetch_warming_state), but the row in the DB
    is on a newer run_id, so the cycle_started upsert's CAS WHERE clause
    matches no rows (rowcount=0 → applied=False). The iteration must turn
    that into ``status='skipped'`` and never reach run_one_cycle. Otherwise
    the stale loop would happily issue Telegram actions (join / read / DM)
    on behalf of a generation that's been replaced.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _seed_channel()
    # enforce_readiness off: this is a stale-cycle CAS test, not the П3 gate.
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)
    # DB row carries the NEW generation already.
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            last_event="queued",
            run_id="run-b",
        ),
    )

    # Lie to the iteration's guard so it proceeds; the CAS underneath will
    # still see run-b and reject the stale UPDATE.
    real_fetch = _loop.fetch_warming_state
    fetch_calls = {"n": 0}

    async def fake_fetch(account_id: str):  # type: ignore[no-untyped-def]
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            real = await real_fetch(account_id)
            if real is None:
                return real
            return real.model_copy(update={"run_id": "run-a"})
        return await real_fetch(account_id)

    monkeypatch.setattr(_loop, "fetch_warming_state", fake_fetch)

    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)

    result = await run_loop_iteration("acc-1", run_id="run-a")
    assert result.status == "skipped"
    assert result.detail == "stale run"
    # The point of the fix: NO Telegram actions on behalf of the stale loop.
    assert recorder.actions == []


@pytest.mark.asyncio
async def test_migration_duplicate_session_name_marks_nulled_accounts_not_alive(
    tmp_path: Path,
) -> None:
    """Round-4 P2.3: nulling a duplicate session_name must also flip status.

    Without flipping status, an account left as ``alive`` after losing its
    session_name silently switches its session file path (``_session_path``
    falls back to account_id when session_name is None) — the operator
    thinks the account is healthy while every runtime action talks to a
    different session file.
    """
    from sqlalchemy import create_engine  # noqa: PLC0415

    from core.migrations import apply_migrations  # noqa: PLC0415

    db_path = tmp_path / "legacy_alive.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    try:
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
                "VALUES ('acc-1', 'shared', 'alive', '2026-01-01', '2026-01-01')",
            )
            connection.exec_driver_sql(
                "INSERT INTO accounts (account_id, session_name, status, created_at, updated_at) "
                "VALUES ('acc-2', 'shared', 'alive', '2026-01-02', '2026-01-02')",
            )
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                "applied_at VARCHAR NOT NULL)",
            )
            for version in (1, 2, 3, 4, 5, 6, 8, 10, 16):
                connection.exec_driver_sql(
                    "INSERT INTO schema_version VALUES (?, 'stub', '2026-01-01')",
                    (version,),
                )

        apply_migrations(engine)

        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT account_id, session_name, status FROM accounts ORDER BY account_id",
            ).all()
            by_id = {str(row[0]): row for row in rows}
            assert by_id["acc-1"][1] == "shared"  # oldest kept its name
            assert by_id["acc-1"][2] == "alive"  # and its status
            assert by_id["acc-2"][1] is None  # duplicate nulled
            assert by_id["acc-2"][2] != "alive"  # AND demoted so operator re-checks
            assert by_id["acc-2"][2] == "new"
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_stale_quarantine_cas_failure_prevents_spam_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-5 P1: a stale quarantine recovery must not hit @SpamBot.

    Round-4 P1.2 closed the regular cycle path, but ``_recover_from_quarantine``
    issued ``_seams.refresh_spam_status(account_id, force=True)`` *before*
    any CAS write — so a stale loop in the quarantine branch would still
    perform external Telegram I/O on behalf of a generation that the
    operator had already replaced.

    Force the race: DB row carries the new generation (``run-b``); lie to
    the iteration's pre-cycle guard so it sees ``run-a`` and steps into
    ``_recover_from_quarantine``. The CAS-gate inside the recovery branch
    must short-circuit before the spam probe fires.
    """
    from services.warming._loop import run_loop_iteration  # noqa: PLC0415

    await create_account(AccountCreate(account_id="acc-1"))
    await _set_settings(chat=False, reactions=False, key="")
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="quarantine",
            quarantine_count=0,
            run_id="run-b",
        ),
    )

    real_fetch = _loop.fetch_warming_state
    fetch_calls = {"n": 0}

    async def fake_fetch(account_id: str):  # type: ignore[no-untyped-def]
        fetch_calls["n"] += 1
        if fetch_calls["n"] == 1:
            real = await real_fetch(account_id)
            if real is None:
                return real
            return real.model_copy(update={"run_id": "run-a"})
        return await real_fetch(account_id)

    monkeypatch.setattr(_loop, "fetch_warming_state", fake_fetch)

    probe_calls: list[str] = []

    async def fake_probe(account_id: str, *, force: bool = False) -> SpamStatusVerdict:  # noqa: ARG001
        probe_calls.append(account_id)
        return SpamStatusVerdict(
            account_id=account_id,
            status="clean",
            detail=None,
            checked_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr(_seams, "refresh_spam_status", fake_probe)

    result = await run_loop_iteration("acc-1", run_id="run-a")
    assert result.status == "skipped"
    assert result.detail == "stale run"
    assert probe_calls == []


@pytest.mark.asyncio
async def test_stale_loop_crash_cannot_overwrite_new_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-6 P1: a crashing stale loop must not stamp 'error' on the new run.

    Without the generation check + CAS in the crash handler, the loop's
    ``except Exception`` branch wrote ``error`` via _set_state without an
    ``expected_run_id``, so a stale generation that fell over after the
    operator restarted the account would overwrite the new generation's
    row with state=error and a misleading ``last_event='loop_crashed'``.
    """
    await create_account(AccountCreate(account_id="acc-1"))
    await upsert_warming_state(
        WarmingStateWrite(account_id="acc-1", state="active", run_id="run-a"),
    )

    monkeypatch.setattr(_runner, "_initial_delay_seconds", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr(_runner, "_loop_sleep_seconds", lambda *_args, **_kwargs: 0.0)

    async def crash_after_replacing_generation(
        account_id: str, *, run_id: str | None = None
    ) -> WarmingCycleResult:
        # A new start_warming raced this iteration: row now carries run-b.
        await upsert_warming_state(
            WarmingStateWrite(account_id=account_id, state="active", run_id="run-b"),
        )
        del run_id  # we are the stale loop; bury our own marker
        msg = "boom from stale loop"
        raise RuntimeError(msg)

    monkeypatch.setattr(_runner, "run_loop_iteration", crash_after_replacing_generation)

    await _runner._warming_loop("acc-1", run_id="run-a")

    state = await fetch_warming_state("acc-1")
    assert state is not None
    # The new generation's row survives — neither state nor run_id was touched.
    assert state.state == "active"
    assert state.run_id == "run-b"
    assert state.last_event != "loop_crashed"
    assert state.last_error is None


# --------------------------------------------------------------------------- #
# Audit #99 — scheduling / readiness consistency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reconcile_parks_unready_account_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconcile must not resurrect an account start_warming would refuse (#99)."""
    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await _seed_channel()
    # No proxy => evaluate_readiness fails, exactly as start_warming would.
    await create_account(AccountCreate(account_id="acc-unready"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-unready", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-unready" not in warming._RUNTIME
    await asyncio.sleep(0)
    assert "acc-unready" not in started
    record = await fetch_warming_state("acc-unready")
    assert record is not None
    assert record.state == "error"
    assert record.last_event == "reconcile_not_ready"
    assert record.last_error


@pytest.mark.asyncio
async def test_reconcile_restarts_ready_account_when_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ready account is still restarted with the readiness gate enabled (#99)."""

    async def fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    await _seed_ready_account("acc-ready")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-ready", state="active"))

    await warming.reconcile_warming_runtime()

    assert "acc-ready" in warming._RUNTIME


@pytest.mark.asyncio
async def test_daily_cap_park_shifts_into_active_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-cap wake honours active hours, not a bare 00:00 UTC instant (#99)."""
    monkeypatch.setattr(settings.warming, "active_hours_enabled", True)
    monkeypatch.setattr(settings.warming, "active_hours_start", 8)
    monkeypatch.setattr(settings.warming, "active_hours_end", 23)

    async def fake_tz(_account_id: str) -> str:
        return "Europe/Istanbul"  # UTC+3, no DST

    monkeypatch.setattr(_loop, "_account_tz", fake_tz)
    await create_account(AccountCreate(account_id="acc-1"))
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

    result = await _loop._gate_daily_limit("acc-1", 3, (3, "2026-06-12"), now, run_id=None)

    assert result is not None
    assert result.detail == "daily limit"
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.next_run_at is not None
    parked = datetime.fromisoformat(record.next_run_at)
    # 00:00 UTC is 03:00 in Istanbul (outside 8-23) → shifted forward to 08:00 local.
    assert parked != _loop._next_utc_midnight(now)
    assert parked.astimezone(ZoneInfo("Europe/Istanbul")).hour == 8


@pytest.mark.asyncio
async def test_summary_ready_counts_only_startable_accounts() -> None:
    """«Готовы» must count startable (idle) accounts, not already-warming ones (#98)."""
    await _seed_ready_account("acc-idle")
    await _seed_ready_account("acc-warm")
    await upsert_warming_state(WarmingStateWrite(account_id="acc-warm", state="active"))

    board = await warming.load_board()

    assert board.summary.warming == 1
    assert board.summary.ready == 1  # only acc-idle; acc-warm is already warming


# --------------------------------------------------------------------------- #
# Audit #100 — cycle resilience / edge cases
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_lone_set_online_failure_sleeps_not_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient SetOnline failure must not park a healthy account in error (#100)."""

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        status = "failed" if action.action_type == "set_online" else "ok"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    await create_account(AccountCreate(account_id="acc-1"))

    await warming.run_loop_iteration("acc-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.state == "sleeping"


@pytest.mark.asyncio
async def test_flood_wait_without_duration_parks_well_into_the_future() -> None:
    """An unknown flood duration must cool down, not collapse to a 0s retry (#100)."""
    before = datetime.now(UTC)
    _, next_run_dt, next_state = await _loop._calculate_next_run(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="flood_wait", flood_wait_seconds=None),
        "normal",
        40,
    )
    assert next_state == "flood_wait"
    floor = settings.warming.flood_wait_fallback_hours * 3600 - 5
    assert (next_run_dt - before).total_seconds() >= floor

    # A concrete duration (even tiny) is still honoured as Telegram instructed.
    _, soon_dt, _ = await _loop._calculate_next_run(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="flood_wait", flood_wait_seconds=5),
        "normal",
        40,
    )
    assert (soon_dt - datetime.now(UTC)).total_seconds() < 60


@pytest.mark.asyncio
async def test_no_reaction_after_failed_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read must not trigger a reaction on the same channel (#100)."""
    seen: list[str] = []

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(action.action_type)
        status = "failed" if action.action_type == "read_channel" else "ok"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(settings.warming, "reaction_probability", 1.0)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert "react_to_post" not in seen
    assert result.reactions_sent == 0


@pytest.mark.asyncio
async def test_cycle_skipped_when_only_set_online_fits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One slot below the cap → park, don't burn a sleep on a presence-only cycle (#100)."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    # Fresh account → intro auto cap of 3 (П2 retired the fleet override);
    # enforce_readiness off so the daily gate fires, not the П3 readiness gate.
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="sleeping",
            daily_actions=2,  # one below the cap of 3 — only SetOnline would fit
            daily_count_date=today,
        ),
    )

    result = await warming.run_loop_iteration("acc-1")

    assert result.status == "skipped"
    assert result.detail == "daily limit"
    assert recorder.actions == []  # no presence-only cycle ran


@pytest.mark.asyncio
async def test_phase_advanced_not_logged_when_finalize_cas_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No phantom phase_advanced when the final CAS write is rejected (#100)."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="run-a",
            current_phase="intro",
        ),
    )
    events: list[str] = []

    async def fake_log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "log_event", fake_log)

    async def rejecting_set_state(
        account_id: str,
        _state: object = None,
        **_kwargs: object,
    ) -> WarmingStateWriteResult:
        # Simulate the CAS rejecting the final write (a newer generation took the
        # row): return applied=False without mutating state.
        record = await fetch_warming_state(account_id)
        assert record is not None
        return WarmingStateWriteResult(record=record, applied=False)

    monkeypatch.setattr(_loop, "_set_state", rejecting_set_state)

    await _loop._finalize_after_cycle(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="ok"),
        365 * 24.0,  # huge age → phase would jump from the stale "intro"
        (0, today),
        (0, datetime.now(UTC), "sleeping"),
        run_id="run-a",
    )

    assert "phase_advanced" not in events


@pytest.mark.asyncio
async def test_phase_advanced_logged_when_finalize_applies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The transition is still announced when the write actually lands (#100)."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state="active",
            run_id="run-a",
            current_phase="intro",
        ),
    )
    events: list[str] = []

    async def fake_log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_loop, "log_event", fake_log)

    await _loop._finalize_after_cycle(
        "acc-1",
        WarmingCycleResult(account_id="acc-1", status="ok"),
        365 * 24.0,
        (0, today),
        (0, datetime.now(UTC), "sleeping"),
        run_id="run-a",
    )

    assert "phase_advanced" in events


# --------------------------------------------------------------------------- #
# Review follow-ups — regressions found while verifying the fixes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reconcile_resumes_quarantine_recovery_despite_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reconcile readiness gate must not abort an engine-managed quarantine recovery."""
    started: list[str] = []

    async def fake_loop(account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        started.append(account_id)
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=True,
        gemini_api_key="",
    )
    # No proxy => evaluate_readiness would fail, but quarantine is engine-managed
    # recovery and must keep running so it can re-probe and recover/escalate.
    await create_account(AccountCreate(account_id="acc-q"))
    await upsert_warming_state(WarmingStateWrite(account_id="acc-q", state="quarantine"))

    await warming.reconcile_warming_runtime()

    assert "acc-q" in warming._RUNTIME
    record = await fetch_warming_state("acc-q")
    assert record is not None
    assert record.state == "quarantine"  # not parked to error by the readiness gate


@pytest.mark.asyncio
async def test_daily_gate_allows_one_cycle_for_a_cap_of_one() -> None:
    """A tiny cap (e.g. a legacy override of 1) must still run once a day, not park forever."""
    await create_account(AccountCreate(account_id="acc-1"))
    today = datetime.now(UTC).date().isoformat()
    now = datetime.now(UTC)

    # cap=1, nothing done yet -> the gate lets the cycle proceed (returns None).
    assert await _loop._gate_daily_limit("acc-1", 1, (0, today), now, run_id=None) is None
    # cap=1, the one action already spent today -> park.
    parked = await _loop._gate_daily_limit("acc-1", 1, (1, today), now, run_id=None)
    assert parked is not None
    assert parked.detail == "daily limit"


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


def test_parse_channels_keeps_case_distinct_invite_hashes() -> None:
    """Invite hashes are case-sensitive; usernames are not (#review)."""
    from services.warming.channels import _parse_channels  # noqa: PLC0415

    assert _parse_channels("t.me/+AbCdEfGh12 t.me/+abcdefgh12") == ["+AbCdEfGh12", "+abcdefgh12"]
    assert _parse_channels("@Alpha @alpha") == ["Alpha"]
