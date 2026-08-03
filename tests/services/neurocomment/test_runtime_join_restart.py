"""The listener's join cache survives a process restart (#40).

``_JOINED_CHANNELS`` is in-memory, so before the join log carried the channel every
restart re-sent ``JoinChannel`` for the whole watch set. Telegram answers "ok" (not
``already_participant``) for a public channel the account is already in, so each
no-op counted against the rolling-24h cap and starved the joins that mattered.
"""

from __future__ import annotations

import pytest

from core.db import (
    count_account_joins_since,
    create_campaign,
    link_channel_to_campaign,
    list_joined_watch_channels,
    list_recent_logs,
    record_join,
)
from schemas.neurocomment import CampaignCreate
from services.neurocomment import _runtime
from tests.services.neurocomment.runtime_support import (
    _drain_joins,
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
)

pytestmark = pytest.mark.usefixtures("isolate_runtime")


@pytest.mark.asyncio
async def test_restart_does_not_rejoin_channels_from_the_join_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()
    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@b")]
    assert await count_account_joins_since("listener-1", "1970-01-01") == 2

    # Restart: the process-lifetime cache is gone, the join log is not.
    _runtime._JOINED_CHANNELS.clear()  # simulates a fresh process
    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@b")]
    assert await count_account_joins_since("listener-1", "1970-01-01") == 2
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_a_kicked_channel_is_rejoined_on_the_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of #40: a cache that never forgets makes a kick permanent.

    The listener only receives updates for channels it is IN, so once the log said
    "joined" the pass skipped the channel forever and the campaign went quiet on it with
    no error anywhere. Only a proven loss re-opens it, and only for that channel — the
    flood guard the join log exists to be must still hold for everything else.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()
    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@b")]

    # Telegram proved the listener is out of @a (kicked / banned / gone private).
    lost = ["@a"]

    def _take(_account_id: str) -> set[str]:
        taken = set(lost)
        lost.clear()  # drains, like the real report
        return taken

    monkeypatch.setattr(_runtime, "take_lost_access_channels", _take)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    # @a joined again; @b never re-sent (its cache entry is untouched).
    assert exec_spy.joined == [
        ("listener-1", "@a"),
        ("listener-1", "@b"),
        ("listener-1", "@a"),
    ]
    # The log is honest either way: the disproven join is still counted (the RPC was
    # spent), and the fresh one is recorded beside it.
    assert await list_joined_watch_channels("listener-1") == {"@a", "@b"}
    assert await count_account_joins_since("listener-1", "1970-01-01") == 3
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_a_channel_that_never_comes_back_stops_being_rejoined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-join must converge on giving up, and the anti-freeze count must only grow.

    A channel whose peer will not resolve refills the lost-access report on EVERY pass, and
    every boot / Start / channel link is a pass. Deleting the join-log row to re-open the
    re-join made the rolling-24h cap structurally unreachable here — one row out, one row
    in, so the count stayed flat at 2 for ever and the brake never engaged. The bound is
    now the pair's own spent attempts.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)
    # @a is lost on every single pass — the shape of a kick from a channel whose handle
    # stops resolving, which is what refills the report unconditionally.
    monkeypatch.setattr(_runtime, "take_lost_access_channels", lambda _account_id: {"@a"})

    counts: list[int] = []
    for _ in range(6):
        await _runtime.reconcile_neurocomment_runtime("listener-1")
        await _drain_joins()
        counts.append(await count_account_joins_since("listener-1", "1970-01-01"))

    # Two joins of @a (the original plus one re-join) and one of @b, then silence.
    assert exec_spy.joined == [
        ("listener-1", "@a"),
        ("listener-1", "@b"),
        ("listener-1", "@a"),
    ]
    # Monotonic, which is what makes the account-wide cap a real backstop.
    assert counts == sorted(counts)
    assert counts[-1] == 3
    logs = await list_recent_logs(limit=100)
    exhausted = [e for e in logs if e.event == "neurocomment_listener_rejoin_exhausted"]
    assert len(exhausted) == 1  # said once, not once per pass
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_discussion_group_joins_never_seed_the_listener_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group join must not stand in for the broadcast channel it belongs to.

    Onboarding records its joins without a channel, so they cannot enter the cache.
    If one ever did, the listener would skip joining that channel and silently receive
    no posts from it — no error, no log, the campaign just goes quiet.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)
    await record_join("listener-1")  # onboarding joined @a's discussion group

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    assert exec_spy.joined == [("listener-1", "@a")]
    await _runtime.shutdown_neurocomment_runtime("listener-1")
