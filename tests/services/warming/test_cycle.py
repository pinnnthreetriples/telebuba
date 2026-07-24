"""Warming tests split from the former service test module: test_cycle.py."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    create_account,
    fetch_warming_state,
    record_dialogue_message,
    save_warming_settings,
)
from core.repositories.logs import list_recent_logs
from schemas.accounts import AccountCreate
from schemas.gemini import GeminiResult
from schemas.warming import (
    WarmingCycleRequest,
)
from services import warming
from services.warming import _seams
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _seed_two_warming_accounts,
    _set_settings,
    _StatusRecorder,
)


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
async def test_cycle_reads_once_and_threads_ids_into_react(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read fetches a channel once; the react reuses those ids (no second fetch)."""
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "reaction_probability", 1.0)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    reads = [a for _id, a in recorder.actions if a.action_type == "read_channel"]
    reacts = [a for _id, a in recorder.actions if a.action_type == "react_to_post"]
    assert len(reads) == 1
    assert len(reacts) == 1
    # The single read fetches the reaction candidate pool, and its ids are
    # threaded into the react so the reactor never re-fetches.
    assert reads[0].message_limit == settings.warming.reaction_message_limit
    assert reacts[0].message_ids == [101, 102]


@pytest.mark.asyncio
async def test_cycle_does_not_count_skipped_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """A react that lands no reaction (message_id=None, e.g. restricted channel) isn't counted."""
    recorder = _Recorder()
    recorder.react_message_id = None  # dispatch attempted, but nothing landed
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "reaction_probability", 1.0)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "ok"
    assert result.reactions_sent == 0
    assert "react_to_post" in recorder.types()  # it did attempt


@pytest.mark.asyncio
async def test_cycle_logs_reaction_skip_when_chance_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reactions on + read ok but the persona dice missed → a 'chance' skip is logged.

    The engine never even dispatches react_to_post here, so this honest breadcrumb
    can only come from the service (not the gateway) — the activity feed shows the
    decision instead of silent inaction.
    """
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 1.0)  # reaction dice always miss
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.reactions_sent == 0
    assert "react_to_post" not in recorder.types()  # dice missed → never dispatched
    skips = [r for r in await list_recent_logs(limit=50) if r.event == "warming_reaction_skipped"]
    assert skips
    assert skips[0].extra["reason"] == "chance"
    assert skips[0].extra["channel"] == "channel_one"


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

    # story view is enabled by default, so the glance lands after the react pass —
    # the rail advances set_online → join → read → react → stories.
    assert steps == ["set_online", "join", "read", "react", "stories"]


@pytest.mark.asyncio
async def test_cycle_omits_stories_step_when_view_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(_seams.rng, "random", lambda: 0.0)
    await _seed_channel()
    await _set_settings(chat=False, reactions=True, key="")

    steps: list[str] = []

    async def _record(step: str) -> None:
        steps.append(step)

    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"), on_step=_record)

    # No story was watched → the rail must not claim the stories step.
    assert "stories" not in steps


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
async def test_cycle_counts_unavailable_actions_as_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``unavailable`` (pool/socket outage) is accounted like ``failed``.

    The status exists so the API can 503 instead of 400; warming must not let
    it fall through its ``== "failed"`` checks as a silent no-op that reports
    an all-dead cycle as ok.
    """
    recorder = _StatusRecorder()
    recorder.status_by_type = {
        "set_online": "ok",
        "join_channel": "unavailable",
        "read_channel": "unavailable",
    }
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "failed"
    assert result.failures >= 2
    assert result.last_failed_action is not None


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
@pytest.mark.parametrize("wait_status", ["slow_mode_wait", "premium_wait"])
async def test_cycle_treats_wait_family_status_as_flood(
    monkeypatch: pytest.MonkeyPatch, wait_status: str
) -> None:
    # FIX #5B: slow_mode_wait / premium_wait were never emitted by any warming
    # test, leaving _WAIT_STATUSES membership unpinned. A join returning either
    # must halt the cycle as a wait (cooldown scheduled) — not "ok", not a plain
    # failure.
    recorder = _StatusRecorder()
    recorder.status_by_type = {"join_channel": wait_status}
    recorder.flood_seconds_by_type = {"join_channel": 300}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 300
    assert result.flood_wait_until is not None
    assert "read_channel" not in recorder.types()  # cycle halted at the join


@pytest.mark.asyncio
async def test_cycle_dm_slow_mode_wait_folds_into_flood(monkeypatch: pytest.MonkeyPatch) -> None:
    # FIX #5A: the DM send path now checks _HALT_STATUSES (was a hardcoded status
    # tuple), so a slow_mode_wait on send_dm is treated as a wait — flood_result
    # set, cycle status flood_wait, no message counted — not a plain failure.
    recorder = _StatusRecorder()
    recorder.status_by_type = {"send_dm": "slow_mode_wait"}
    recorder.flood_seconds_by_type = {"send_dm": 300}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)

    async def fake_generate(_request: object) -> GeminiResult:
        return GeminiResult(status="ok", text="hi there")

    monkeypatch.setattr(_seams, "generate_text", fake_generate)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key="gemini-key")
    await _seed_two_warming_accounts()
    await record_dialogue_message("acc-2", "acc-1", "привет!", replied=False)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 300
    assert result.messages_sent == 0
    assert result.last_failed_action == "send_dm"


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
