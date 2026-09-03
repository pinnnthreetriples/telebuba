"""Discovery stage 2 — the comments-enabled probe: cache use, pacing, abort rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import create_campaign
from core.repositories.neurocomment import (
    fetch_linked_group,
    list_discovery_candidates,
    replace_discovery_candidates,
    upsert_linked_group,
)
from schemas.neurocomment import CampaignCreate
from schemas.neurocomment_discovery import DiscoveryCandidateRow
from schemas.telegram_actions import LinkedDiscussionGroupResult
from services.neurocomment import _discovery_state, _seams
from services.neurocomment._discovery_qualify import run_qualification
from services.neurocomment._state import set_cooldown
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    flood_error,
    pool_of,
    read_error,
    search_request,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


def _row(channel: str, *, subscribers: int | None = None) -> DiscoveryCandidateRow:
    return DiscoveryCandidateRow(
        channel=channel,
        title=channel.title(),
        subscribers=subscribers,
        source="telegram_search",
    )


async def _seed(*channels: str) -> str:
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))
    await replace_discovery_candidates(campaign.campaign_id, [_row(ch) for ch in channels])
    return campaign.campaign_id


async def _backdate(channel: str, checked_at: datetime | str) -> None:
    """Rewrite a cache row's stamp so freshness can be exercised without waiting."""
    stamp = checked_at if isinstance(checked_at, str) else checked_at.isoformat()
    from core.db import _get_engine  # noqa: PLC0415

    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_linked_groups SET checked_at = ? WHERE channel = ?",
            (stamp, channel),
        )


def _verdict(*, enabled: bool, count: int | None = None) -> LinkedDiscussionGroupResult:
    return LinkedDiscussionGroupResult(
        linked_chat_id=-100 if enabled else None,
        comments_enabled=enabled,
        participants_count=count,
    )


@pytest.mark.asyncio
async def test_probe_records_the_verdict_and_refreshes_the_shared_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True, count=777))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed("alpha")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert rows[0].qualified_at is not None
    assert rows[0].qualify_error is None
    # The subscriber count rides the same RPC — free backfill.
    assert rows[0].subscribers == 777
    cached = await fetch_linked_group("alpha")
    assert cached is not None
    assert cached.comments_enabled == 1


@pytest.mark.asyncio
async def test_the_probe_keeps_every_fitness_signal_of_the_one_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All of this rides the getFullChannel reply the probe already spends.

    Throwing it away for one bool is what left the board unable to say WHY a channel is
    unusable — and re-learning any of it would cost another RPC per candidate.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(
            linked=lambda _action: LinkedDiscussionGroupResult(
                linked_chat_id=-100,
                comments_enabled=True,
                group_slowmode_enabled=True,
                join_to_send=True,
                join_request=True,
                can_send_messages=False,
                scam=True,
                fake=False,
                restricted=True,
            ),
        ),
    )
    campaign_id = await _seed("gated")

    await run_qualification(campaign_id, pool_of(), search_request())

    verdict = _discovery_state.verdicts(campaign_id)["gated"]
    # ``comments_enabled`` is deliberately absent: it duplicated the candidate's own
    # ``qualification`` field, which is what the board actually renders.
    assert verdict.model_dump() == {
        "can_send_messages": False,
        "join_to_send": True,
        "join_request": True,
        "group_slowmode_enabled": True,
        "scam": True,
        "fake": False,
        "restricted": True,
        # Derived from the same reply: the reply said nothing about the TARGET's join
        # gate (``join_request`` above is the linked group's), so access is unknown — not
        # "open" — the title reads as English, and no category was asked for.
        "access": None,
        "language": "en",
        "is_group": None,
        "category_match": None,
    }


@pytest.mark.asyncio
async def test_a_signal_the_reply_omits_stays_unknown_rather_than_a_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` means "the reply did not answer", so nothing may flatten it to ``False``.

    A channel blocked on a field Telegram simply omitted (older TL layer, no linked
    group) would be refused for a gate that was never measured.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=lambda _action: _verdict(enabled=True)),
    )
    campaign_id = await _seed("quiet")

    await run_qualification(campaign_id, pool_of(), search_request())

    verdict = _discovery_state.verdicts(campaign_id)["quiet"]
    assert verdict.can_send_messages is None
    assert verdict.join_to_send is None
    assert verdict.join_request is None
    assert verdict.restricted is None


@pytest.mark.asyncio
async def test_an_unanswerable_probe_records_no_verdict_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed probe learnt nothing, so the board must read fitness as unknown."""
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=read_error("RPC: ChannelPrivateError")),
    )
    campaign_id = await _seed("broken")

    await run_qualification(campaign_id, pool_of(), search_request())

    assert _discovery_state.verdicts(campaign_id) == {}


@pytest.mark.asyncio
async def test_a_cache_hit_spends_no_rpc_and_so_carries_no_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fitness signals have no column, so a cached channel has none to report.

    Deliberate: the cheap re-search is worth more than a full verdict, and the board
    still knows from the cache whether comments are on.
    """
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=lambda _action: _verdict(enabled=True)),
    )
    campaign_id = await _seed("known")
    await upsert_linked_group("known", -100, comments_enabled=True)

    await run_qualification(campaign_id, pool_of(), search_request())

    assert _discovery_state.verdicts(campaign_id) == {}


@pytest.mark.asyncio
async def test_comments_off_is_recorded_as_a_successful_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=lambda _action: _verdict(enabled=False)),
    )
    campaign_id = await _seed("nocomments")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert rows[0].qualify_error is None
    cached = await fetch_linked_group("nocomments")
    assert cached is not None
    assert cached.comments_enabled == 0


@pytest.mark.asyncio
async def test_fresh_cache_hit_costs_zero_rpcs_and_zero_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is what makes a re-search over familiar keywords finish instantly."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(_seams, "sleep", _record)
    campaign_id = await _seed("known")
    await upsert_linked_group("known", -100, comments_enabled=True)

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    assert reader.calls == []
    assert slept == []
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert rows[0].qualified_at is not None


@pytest.mark.asyncio
async def test_stale_cache_entry_is_reprobed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A channel that switched comments on must not stay filtered out forever."""
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_linked_group_ttl_hours", 24)
    campaign_id = await _seed("stale")
    await upsert_linked_group("stale", None, comments_enabled=False)
    await _backdate("stale", datetime.now(UTC) - timedelta(days=30))

    await run_qualification(campaign_id, pool_of(), search_request())

    assert len(reader.calls) == 1
    cached = await fetch_linked_group("stale")
    assert cached is not None
    assert cached.comments_enabled == 1


@pytest.mark.asyncio
async def test_the_ttl_is_read_in_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stamp just past the window must re-probe: the unit itself has to be pinned.

    The other TTL tests use a 30x margin, so any wrong unit inside a factor of 24
    survives them — and a cache held 24x too long would filter a channel that switched
    comments on out of every campaign for months.
    """
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_linked_group_ttl_hours", 24)
    campaign_id = await _seed("edge")
    await upsert_linked_group("edge", None, comments_enabled=False)
    await _backdate("edge", datetime.now(UTC) - timedelta(hours=25))

    await run_qualification(campaign_id, pool_of(), search_request())

    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_an_unparseable_cache_stamp_is_treated_as_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive, and reachable: the column is text a legacy row could have written."""
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed("garbled")
    await upsert_linked_group("garbled", -100, comments_enabled=True)
    await _backdate("garbled", "not-a-timestamp")

    await run_qualification(campaign_id, pool_of(), search_request())

    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_progress_is_signalled_during_a_long_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a nudge the modal sits frozen on "qualifying" for minutes."""
    frames: list[int] = []
    monkeypatch.setattr(
        "services.neurocomment._discovery_qualify.signal_discovery_progress",
        lambda: frames.append(1),
    )
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=lambda _action: _verdict(enabled=True)),
    )
    campaign_id = await _seed(*[f"chan_{index:02d}" for index in range(11)])

    await run_qualification(campaign_id, pool_of(), search_request())

    # _PROGRESS_EVERY is 5, so an 11-candidate pass nudges at 5 and 10.
    assert len(frames) == 2


@pytest.mark.asyncio
async def test_zero_ttl_disables_the_cache_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_linked_group_ttl_hours", 0)
    campaign_id = await _seed("known")
    await upsert_linked_group("known", -100, comments_enabled=True)

    await run_qualification(campaign_id, pool_of(), search_request())

    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_pacing_sleeps_between_real_rpcs_only(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(_seams, "sleep", _record)
    monkeypatch.setattr(settings.neurocomment, "discovery_qualify_delay_min_seconds", 1.25)
    monkeypatch.setattr(settings.neurocomment, "discovery_qualify_delay_max_seconds", 1.25)
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=lambda _action: _verdict(enabled=True)),
    )
    campaign_id = await _seed("aaa", "bbb", "cached")
    await upsert_linked_group("cached", -100, comments_enabled=True)

    await run_qualification(campaign_id, pool_of(), search_request())

    # Two real probes -> one gap; the cache hit contributes nothing.
    assert slept == [1.25]


@pytest.mark.asyncio
async def test_flood_wait_aborts_and_leaves_the_tail_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying into a rate limit is how a soft limit becomes a hard one."""
    reader = ReadRecorder(linked=flood_error(300))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed("aaa", "bbb", "ccc")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason == "FloodWait(300s)"
    assert len(reader.calls) == 1
    rows = {row.channel: row for row in (await list_discovery_candidates(campaign_id)).rows}
    # Nothing is marked, not even the sacrificed candidate: a flood wait says nothing
    # about the channel, and stamping it would leave a permanent "could not check"
    # verdict that only a full re-search clears. The whole tail stays resumable.
    assert rows["aaa"].qualified_at is None
    assert rows["bbb"].qualified_at is None
    assert rows["ccc"].qualified_at is None


@pytest.mark.asyncio
async def test_a_cooldown_recorded_mid_pass_stops_the_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hundred probes is minutes; a limit landing at probe two must stop probe three.

    The pass has two abort counters and neither can see this — nothing it asked for
    failed. Stops like a FloodWait does: nothing extra is marked, the tail stays pending
    and resumable, and the reason says the account is cooling rather than returning
    ``None`` for "finished".
    """
    probes = 0

    async def _probe(_account_id: str, _action: object) -> LinkedDiscussionGroupResult:
        nonlocal probes
        probes += 1
        if probes == 2:
            # The comment engine parking the same listener, mid-pass.
            await set_cooldown(LISTENER_ID, datetime.now(UTC) + timedelta(hours=1))
        return _verdict(enabled=True)

    monkeypatch.setattr(_seams, "execute_read", _probe)
    campaign_id = await _seed("aaa", "bbb", "ccc", "ddd")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason == "account_cooling"
    assert probes == 2
    rows = {row.channel: row for row in (await list_discovery_candidates(campaign_id)).rows}
    assert rows["aaa"].qualified_at is not None
    assert rows["bbb"].qualified_at is not None
    assert rows["ccc"].qualified_at is None
    assert rows["ddd"].qualified_at is None


@pytest.mark.asyncio
async def test_a_cooldown_landing_during_the_pace_sleep_costs_no_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check belongs after the sleep, not before it.

    Every probe but the first is preceded by a one-to-two second pace sleep — the widest
    window in the pass, and the likeliest moment for somebody else's flood to land. Read
    before the sleep, the check was answering about a moment that had already passed and
    still bought one RPC into the live window.
    """
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))

    async def _flood_while_pacing(_seconds: float) -> None:
        await set_cooldown(LISTENER_ID, datetime.now(UTC) + timedelta(hours=1))

    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(_seams, "sleep", _flood_while_pacing)
    campaign_id = await _seed("aaa", "bbb", "ccc")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason == "account_cooling"
    # Only the first probe, which spends no pace sleep. The second never fires.
    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_a_channel_scoped_cooldown_does_not_stop_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass reads with the account; a slow-mode window on one chat is not its limit."""
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    await set_cooldown(LISTENER_ID, datetime.now(UTC) + timedelta(hours=1), channel="@chat")
    campaign_id = await _seed("aaa", "bbb")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    assert len(reader.calls) == 2


@pytest.mark.asyncio
async def test_a_single_error_marks_one_candidate_and_the_loop_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=read_error("RPC: ChannelPrivateError", only="broken")),
    )
    campaign_id = await _seed("aaa", "broken", "zzz")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    rows = {row.channel: row for row in (await list_discovery_candidates(campaign_id)).rows}
    assert rows["broken"].qualify_error == "RPC: ChannelPrivateError"
    assert rows["zzz"].qualified_at is not None
    assert rows["zzz"].qualify_error is None


@pytest.mark.asyncio
async def test_consecutive_errors_abort_the_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead session must not burn one RPC per remaining candidate."""
    reader = ReadRecorder(linked=read_error("RPC: AuthKeyUnregisteredError"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    campaign_id = await _seed("aaa", "bbb", "ccc", "ddd", "eee")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason == "RPC: AuthKeyUnregisteredError"
    assert len(reader.calls) == 2


@pytest.mark.asyncio
async def test_a_success_resets_the_consecutive_error_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = ReadRecorder(linked=read_error("RPC: TimeoutError", only="bad"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 2)
    # Probing follows channel order, so the names interleave bad/good alphabetically
    # and the counter never reaches two failures in a row.
    campaign_id = await _seed("aa_bad", "bb_good", "cc_bad", "dd_good")

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    assert len(reader.calls) == 4


@pytest.mark.asyncio
async def test_a_pass_that_fails_every_other_probe_aborts_once_the_rate_is_measurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consecutive counter never trips on a half-dead session; the rate rule must."""
    reader = ReadRecorder(linked=read_error("RPC: TimeoutError", only="bad"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    # Alternating, so three failures never land in a row whatever the consecutive bound.
    channels = [f"c{index:02d}_{'bad' if index % 2 else 'good'}" for index in range(30)]
    campaign_id = await _seed(*channels)

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason == "RPC: TimeoutError"
    # _ERROR_RATE_MIN_PROBES is 20, and the 20th probe is this pattern's 10th failure.
    assert len(reader.calls) == 20
    rows = {row.channel: row for row in (await list_discovery_candidates(campaign_id)).rows}
    assert rows["c19_bad"].qualify_error == "RPC: TimeoutError"
    # The untouched tail stays pending, so the next pass resumes exactly here.
    assert all(rows[channel].qualified_at is None for channel in channels[20:])


@pytest.mark.asyncio
async def test_a_healthy_sweep_with_a_minority_of_dead_handles_runs_to_the_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dozen dead or private handles in a hundred is an ordinary sweep, not a failure.

    Aborting here would be permanent, not merely wasteful: a re-search re-inserts every
    candidate with ``qualified_at = NULL`` and probing follows channel order, so the pass
    would stop at the same handle every time and the tail could never be qualified.
    """
    reader = ReadRecorder(linked=read_error("RPC: ChannelPrivateError", only="dead"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    channels = [f"c{index:03d}_{'dead' if index % 8 == 7 else 'live'}" for index in range(100)]
    campaign_id = await _seed(*channels)

    reason = await run_qualification(campaign_id, pool_of(), search_request())

    assert reason is None
    assert len(reader.calls) == 100
    rows = (await list_discovery_candidates(campaign_id)).rows
    assert all(row.qualified_at is not None for row in rows)
    assert sum(row.qualify_error is not None for row in rows) == 12


@pytest.mark.asyncio
async def test_nothing_pending_is_a_cheap_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))

    reason = await run_qualification(campaign.campaign_id, pool_of(), search_request())

    assert reason is None
    assert reader.calls == []


@pytest.mark.asyncio
async def test_qualification_resumes_only_unprobed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``qualified_at`` is what makes an aborted pass resumable."""
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed("aaa", "bbb")
    from core.repositories.neurocomment import mark_discovery_qualified  # noqa: PLC0415

    await mark_discovery_qualified(campaign_id, "aaa")

    await run_qualification(campaign_id, pool_of(), search_request())

    probed = [getattr(call, "channel", None) for call in reader.calls]
    assert probed == ["bbb"]
