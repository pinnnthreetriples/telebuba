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
from services.neurocomment import _seams
from services.neurocomment._discovery_qualify import run_qualification
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    read_error,
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

    reason = await run_qualification(campaign_id, LISTENER_ID)

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
async def test_comments_off_is_recorded_as_a_successful_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=lambda _action: _verdict(enabled=False)),
    )
    campaign_id = await _seed("nocomments")

    reason = await run_qualification(campaign_id, LISTENER_ID)

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

    reason = await run_qualification(campaign_id, LISTENER_ID)

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

    await run_qualification(campaign_id, LISTENER_ID)

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

    await run_qualification(campaign_id, LISTENER_ID)

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

    await run_qualification(campaign_id, LISTENER_ID)

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

    await run_qualification(campaign_id, LISTENER_ID)

    # _PROGRESS_EVERY is 5, so an 11-candidate pass nudges at 5 and 10.
    assert len(frames) == 2


@pytest.mark.asyncio
async def test_zero_ttl_disables_the_cache_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_linked_group_ttl_hours", 0)
    campaign_id = await _seed("known")
    await upsert_linked_group("known", -100, comments_enabled=True)

    await run_qualification(campaign_id, LISTENER_ID)

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

    await run_qualification(campaign_id, LISTENER_ID)

    # Two real probes -> one gap; the cache hit contributes nothing.
    assert slept == [1.25]


@pytest.mark.asyncio
async def test_flood_wait_aborts_and_leaves_the_tail_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying into a rate limit is how a soft limit becomes a hard one."""
    reader = ReadRecorder(linked=read_error("FloodWait(300s)"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed("aaa", "bbb", "ccc")

    reason = await run_qualification(campaign_id, LISTENER_ID)

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
async def test_a_single_error_marks_one_candidate_and_the_loop_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _seams,
        "execute_read",
        ReadRecorder(linked=read_error("RPC: ChannelPrivateError", only="broken")),
    )
    campaign_id = await _seed("aaa", "broken", "zzz")

    reason = await run_qualification(campaign_id, LISTENER_ID)

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

    reason = await run_qualification(campaign_id, LISTENER_ID)

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

    reason = await run_qualification(campaign_id, LISTENER_ID)

    assert reason is None
    assert len(reader.calls) == 4


@pytest.mark.asyncio
async def test_total_errors_abort_a_pass_that_fails_every_other_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consecutive counter never trips on a half-dead session; the total one must."""
    reader = ReadRecorder(linked=read_error("RPC: TimeoutError", only="bad"))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_consecutive_errors", 3)
    monkeypatch.setattr(settings.neurocomment, "discovery_max_total_errors", 2)
    campaign_id = await _seed("aa_bad", "bb_good", "cc_bad", "dd_good", "ee_good")

    reason = await run_qualification(campaign_id, LISTENER_ID)

    assert reason == "RPC: TimeoutError"
    # aa_bad, bb_good, cc_bad — the second failure ends the pass.
    assert len(reader.calls) == 3
    rows = {row.channel: row for row in (await list_discovery_candidates(campaign_id)).rows}
    # The untouched tail stays pending, so the next pass resumes exactly here.
    assert rows["dd_good"].qualified_at is None
    assert rows["ee_good"].qualified_at is None
    assert rows["cc_bad"].qualify_error == "RPC: TimeoutError"


@pytest.mark.asyncio
async def test_nothing_pending_is_a_cheap_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign = await create_campaign(CampaignCreate(name="C", prompt="p"))

    reason = await run_qualification(campaign.campaign_id, LISTENER_ID)

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

    await run_qualification(campaign_id, LISTENER_ID)

    probed = [getattr(call, "channel", None) for call in reader.calls]
    assert probed == ["bbb"]
