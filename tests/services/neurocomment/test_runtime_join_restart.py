"""The listener's join cache survives a process restart (#40).

``_JOINED_CHANNELS`` is in-memory, so before the join log carried the channel every
restart re-sent ``JoinChannel`` for the whole watch set. Telegram answers "ok" (not
``already_participant``) for a public channel the account is already in, so each
no-op counted against the rolling-24h cap and starved the joins that mattered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.db import (  # type: ignore[attr-defined]
    _get_engine,
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


def _age_losses(account_id: str, hours: float) -> None:
    """Push a listener's stamped losses ``hours`` into the past, keeping them distinct.

    Distinct because the attempt count is over DISTINCT ``lost_at``: collapsing them onto
    one instant would read as a single attempt whether the window applies or not.
    """
    with _get_engine().begin() as connection:
        ids = [
            int(row[0])
            for row in connection.exec_driver_sql(
                "SELECT id FROM neurocomment_join_log WHERE account_id = ? "
                "AND lost_at IS NOT NULL ORDER BY id",
                (account_id,),
            )
        ]
        for offset, row_id in enumerate(ids):
            aged = datetime.now(UTC) - timedelta(hours=hours, minutes=offset)
            connection.exec_driver_sql(
                "UPDATE neurocomment_join_log SET lost_at = ? WHERE id = ?",
                (aged.isoformat(), row_id),
            )


@pytest.mark.asyncio
async def test_the_give_up_expires_once_its_losses_leave_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The give-up must self-heal, because nothing in the product can clear it by hand.

    Nothing ever resets ``lost_at`` (unlike both sibling budgets, which are zeroed on
    approval and on regained access), and the retention purge may never run at all —
    ``retention_days=0`` keeps rows for ever. So an all-time count made two transient
    losses months apart a permanent silence: an admin re-invite, relinking the channel,
    a campaign restart and every reconcile all leave it exhausted.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)
    monkeypatch.setattr(_runtime, "take_lost_access_channels", lambda _account_id: {"@a"})

    for _ in range(4):
        await _runtime.reconcile_neurocomment_runtime("listener-1")
        await _drain_joins()
    # Budget spent: the original join plus one re-join, then the pass gives up.
    assert exec_spy.joined == [("listener-1", "@a"), ("listener-1", "@a")]
    # And gives up QUIETLY: a given-up channel is evicted from the cache when its last row
    # is stamped, so the untracked-loss report has nothing to say about it — without that
    # gate it would print once per reconcile, for ever, at the operator.
    events = [e.event for e in await list_recent_logs(limit=100)]
    assert "neurocomment_listener_access_lost_untracked" not in events

    # A week passes with the channel healthy — the same rows, still counted by the join cap.
    _age_losses("listener-1", hours=200.0)
    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    assert exec_spy.joined == [
        ("listener-1", "@a"),
        ("listener-1", "@a"),
        ("listener-1", "@a"),  # eligible again, without an operator touching the database
    ]
    assert await count_account_joins_since("listener-1", "1970-01-01") == 3
    await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_a_loss_with_no_join_row_to_charge_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pair stays cached (correctly) and unwatched (silently) — so it must say so.

    Reachable whenever a cached pair carries no standing row: the ``already_participant``
    route caches without recording one, and the retention purge can take one away. Not
    evicting is deliberate — with nothing to count, re-opening the pair would be a re-join
    no budget could ever bound — but the channel then receives nothing until a restart.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    exec_spy = _ExecuteSpy()
    _patch_execute(monkeypatch, exec_spy)

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()
    assert exec_spy.joined == [("listener-1", "@a")]
    # The retention purge takes the standing row; the in-memory cache still holds the pair.
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("DELETE FROM neurocomment_join_log")
    monkeypatch.setattr(_runtime, "take_lost_access_channels", lambda _account_id: {"@a"})

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    await _drain_joins()

    assert exec_spy.joined == [("listener-1", "@a")]  # not re-joined, and not re-charged
    untracked = [
        entry
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_listener_access_lost_untracked"
    ]
    assert len(untracked) == 1
    # And no position in the re-join budget, because nothing was charged to it: the
    # sibling lines' "1/2" beside this one would say a countdown had started.
    assert "reason" not in untracked[0].extra
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


@pytest.mark.asyncio
async def test_each_lost_access_line_says_which_rejoin_it_is_out_of_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The feed counts the re-joins out — "1/2", then "2/2" — as the budget is spent.

    ``attempts`` was already in ``extra``, where only a developer looks, so in the feed a
    channel on its last re-join read exactly like one on its first and the give-up arrived
    without warning. The give-up line closes the run at the budget rather than reporting a
    count of its own: only losses inside the rolling window count, so a window that rolls
    under a channel losing access over and over can hand back more of them than the budget
    has room for, and "3/2" would read as arithmetic gone wrong.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())
    # Lost on every pass — the channel whose handle stops resolving.
    monkeypatch.setattr(_runtime, "take_lost_access_channels", lambda _account_id: {"@a"})

    for _ in range(4):  # join, lose it twice, then the budget is gone
        await _runtime.reconcile_neurocomment_runtime("listener-1")
        await _drain_joins()

    entries = list(reversed(await list_recent_logs(limit=100)))
    counters = {
        event: [e.extra.get("reason") for e in entries if e.event == event]
        for event in ("neurocomment_listener_access_lost", "neurocomment_listener_rejoin_exhausted")
    }
    assert counters["neurocomment_listener_access_lost"] == ["1/2"]
    assert counters["neurocomment_listener_rejoin_exhausted"] == ["2/2"]
    await _runtime.shutdown_neurocomment_runtime("listener-1")
