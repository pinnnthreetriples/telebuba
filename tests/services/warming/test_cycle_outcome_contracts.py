"""Full-cycle outcomes, budgets, cleanup, and multi-channel aggregation."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import save_warming_settings
from schemas.telegram_actions import ActionResult, TelegramAction
from schemas.warming import AddChannelsRequest, WarmingCycleRequest
from services import warming
from services.warming import _cycle, _seams
from tests.services.warming._support import _seed_channel, _set_settings, _StatusRecorder


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("failed", "failed"),
        ("unavailable", "failed"),
        ("peer_flood", "peer_flood"),
        ("flood_wait", "flood_wait"),
        ("slow_mode_wait", "flood_wait"),
    ],
)
async def test_online_failure_matrix_stops_before_channel_work(
    monkeypatch: pytest.MonkeyPatch, status: str, expected_status: str
) -> None:
    recorder = _StatusRecorder()
    recorder.status_by_type = {"set_online": status}
    recorder.flood_seconds_by_type = {"set_online": 45}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == expected_status
    assert result.attempted_actions == 1
    assert recorder.types() == ["set_online"]
    assert result.channels_joined == 0
    assert result.channels_read == 0
    if expected_status == "failed":
        assert result.failures == 1
        assert result.last_failed_action == "set_online"
    else:
        assert result.failures == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("budget", "expected_actions"),
    [(0, []), (1, ["set_online", "set_online"]), (2, ["set_online", "join_channel", "set_online"])],
)
async def test_cycle_never_exceeds_hard_action_budget(
    monkeypatch: pytest.MonkeyPatch,
    budget: int,
    expected_actions: list[str],
) -> None:
    recorder = _StatusRecorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(
        WarmingCycleRequest(account_id="acc-1", remaining_actions=budget)
    )

    assert result.status == "ok"
    assert result.attempted_actions == budget
    assert recorder.types() == expected_actions


@pytest.mark.asyncio
async def test_failed_join_continues_to_read_and_preserves_failure_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _StatusRecorder()
    recorder.status_by_type = {"join_channel": "unavailable"}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "failed"
    assert result.failures == 1
    assert result.channels_joined == 0
    assert result.channels_read == 1
    assert result.last_failed_action == "join"
    assert result.last_failed_channel == "channel_one"
    assert recorder.types() == ["set_online", "join_channel", "read_channel", "set_online"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["peer_flood", "premium_wait"])
async def test_read_limit_halts_reaction_stories_and_chat(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    recorder = _StatusRecorder()
    recorder.status_by_type = {"read_channel": status}
    recorder.flood_seconds_by_type = {"read_channel": 90}
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=True, reactions=True, key="gemini-key")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1", dm_allowed=True))

    assert result.status == ("peer_flood" if status == "peer_flood" else "flood_wait")
    assert result.last_failed_action == "read_channel"
    assert result.last_failed_channel == "channel_one"
    assert "react_to_post" not in recorder.types()
    assert "watch_peer_stories" not in recorder.types()
    assert "send_dm" not in recorder.types()
    assert recorder.types()[-1] == "set_online"  # offline cleanup still lands


@pytest.mark.asyncio
async def test_multi_channel_tallies_accumulate_without_counting_offline_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _StatusRecorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 2)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 2)
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    monkeypatch.setattr(_seams.rng, "randint", lambda lower, _upper: lower)
    monkeypatch.setattr(_seams.rng, "sample", lambda population, count: population[:count])
    await warming.add_channels(AddChannelsRequest(raw="@one\n@two"))
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "ok"
    assert result.channels_joined == 2
    assert result.channels_read == 2
    assert result.attempted_actions == 5  # online + 2 joins + 2 reads
    assert recorder.types().count("set_online") == 2
    assert recorder.types().count("join_channel") == 2
    assert recorder.types().count("read_channel") == 2


@pytest.mark.asyncio
async def test_later_peer_flood_outranks_earlier_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await warming.add_channels(AddChannelsRequest(raw="@one\n@two"))
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        join_enabled=True,
        gemini_api_key="",
    )
    monkeypatch.setattr(settings.warming, "channels_per_cycle_min", 2)
    monkeypatch.setattr(settings.warming, "channels_per_cycle_max", 2)
    monkeypatch.setattr(settings.warming, "story_view_enabled", False)
    monkeypatch.setattr(_seams.rng, "randint", lambda lower, _upper: lower)
    monkeypatch.setattr(_seams.rng, "sample", lambda population, count: population[:count])
    joins = 0
    seen: list[tuple[str, str | None]] = []

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        nonlocal joins
        channel = getattr(action, "channel", None)
        seen.append((action.action_type, channel))
        status = "ok"
        if action.action_type == "join_channel":
            joins += 1
            status = "failed" if joins == 1 else "peer_flood"
        return ActionResult(status=status, action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(_seams, "execute", execute)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "peer_flood"
    assert result.failures == 1
    assert result.channels_read == 1
    assert result.last_failed_action == "join"
    assert result.last_failed_channel == seen[-2][1]
    assert [kind for kind, _channel in seen].count("read_channel") == 1


@pytest.mark.asyncio
async def test_offline_cleanup_failure_is_logged_without_reclassifying_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")
    events: list[tuple[str, dict[str, object]]] = []

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        if action.action_type == "set_online" and action.online is False:
            message = "disconnect failed"
            raise RuntimeError(message)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    async def log(_level: str, event: str, **kwargs: object) -> None:
        events.append((event, kwargs))

    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_cycle, "log_event", log)

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "ok"
    assert result.channels_joined == 1
    assert result.channels_read == 1
    names = [event for event, _kwargs in events]
    assert names == ["warming_set_offline_failed", "warming_cycle_completed"]
    assert events[0][1]["extra"] == {
        "error_type": "RuntimeError",
        "message": "disconnect failed",
    }


@pytest.mark.asyncio
async def test_progress_callback_failure_still_runs_offline_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _StatusRecorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await _seed_channel()
    await _set_settings(chat=False, reactions=False, key="")

    async def fail_progress(step: str) -> None:
        assert step == "set_online"
        message = "progress sink down"
        raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="progress sink down"):
        await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"), on_step=fail_progress)

    assert recorder.types() == ["set_online", "set_online"]
