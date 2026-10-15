"""Comment threads and public groups (item 4.6): read without joining, groups from their phase."""

from __future__ import annotations

import pytest

from core.config import settings
from core.repositories.warming_pool import add_found_warming_channels
from schemas._warming_visits import VisitPlan
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    WarmingChannel,
    WarmingCycleRequest,
)
from services import warming
from services.warming import _seams
from services.warming._social_reads import reaction_chance, walkable_channels
from tests.services.warming._support import _Recorder, _set_settings


def _channel(name: str, **over: object) -> WarmingChannel:
    base: dict[str, object] = {"channel": name, "created_at": "2026-01-01T00:00:00+00:00"}
    base.update(over)
    return WarmingChannel.model_validate(base)


def test_groups_wait_for_their_phase_and_removed_finds_are_never_walked() -> None:
    pool = [
        _channel("@chan"),
        _channel("@group", kind="group"),
        _channel("@removed", status="removed", source="discovery"),
    ]
    assert [c.channel for c in walkable_channels(pool, "settling")] == ["@chan"]
    assert [c.channel for c in walkable_channels(pool, "warming")] == ["@chan", "@group"]


def test_a_group_is_reacted_to_less_often() -> None:
    factor = settings.warming.group_reaction_factor
    assert reaction_chance(0.5, _channel("@g", kind="group")) == pytest.approx(0.5 * factor)
    assert reaction_chance(0.5, _channel("@c", kind="channel")) == 0.5


@pytest.mark.asyncio
async def test_a_binge_opens_the_comments_without_joining_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.warming, "discussion_binge_probability", 1.0)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await warming.add_channels(AddChannelsRequest(raw="@news_one"))
    await _set_settings(chat=False, reactions=False, key="")

    await warming.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-1",
            visit=VisitPlan(kind="binge", steps=["set_online", "join", "read"], channels=1),
        ),
    )

    types = recorder.types()
    assert types.index("warm_read_discussion") == types.index("read_channel") + 1
    assert "join_discussion_group" not in types
    action = next(a for _id, a in recorder.actions if a.action_type == "warm_read_discussion")
    assert getattr(action, "message_id", None) in {101, 102}


@pytest.mark.asyncio
async def test_a_glance_never_opens_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.warming, "discussion_probability", 1.0)
    monkeypatch.setattr(settings.warming, "discussion_binge_probability", 1.0)
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await warming.add_channels(AddChannelsRequest(raw="@news_one"))
    await _set_settings(chat=False, reactions=False, key="")

    await warming.run_one_cycle(
        WarmingCycleRequest(
            account_id="acc-1",
            visit=VisitPlan(kind="glance", steps=["set_online", "read"], channels=1),
        ),
    )
    assert "warm_read_discussion" not in recorder.types()


@pytest.mark.asyncio
async def test_a_removed_find_alone_is_no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    await add_found_warming_channels([_channel("found_one", source="discovery")], limit=10)
    await warming.remove_channel(RemoveChannelRequest(channel="found_one"))
    await _set_settings(chat=False, reactions=False, key="")

    result = await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))

    assert result.status == "skipped"
    assert recorder.actions == []
