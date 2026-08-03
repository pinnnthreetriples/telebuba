"""Tests for neurocomment runtime sweep behavior."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (  # type: ignore[attr-defined]
    _get_engine,
    assign_account_to_campaign,
    claim_comment,
    count_account_channel_comments_since,
    count_account_joins_since,
    create_account,
    create_campaign,
    fetch_comment,
    link_channel_to_campaign,
    list_campaign_channels,
    list_recent_logs,
    mark_comment_posted,
    record_join,
    stamp_join_request,
    upsert_readiness,
)
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from schemas.telegram_actions import (
    CheckMessagesAlive,
    CheckMessagesAliveResult,
)
from services.neurocomment import _rejoin, _runtime, _state, _sweep
from tests.services.neurocomment.runtime_support import (
    _ExecuteSpy,
    _ListenerSpy,
    _patch_execute,
    _patch_listener,
)

if TYPE_CHECKING:
    from schemas.logs import LogEntry

pytestmark = pytest.mark.usefixtures("isolate_runtime")

# --------------------------------------------------------------------------- #
# Deletion sweep (#131): periodic re-read → escalating channel back-off.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_reconcile_starts_sweep_and_shutdown_cancels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    try:
        assert _runtime._SWEEP_TASK is not None
        assert not _runtime._SWEEP_TASK.done()
    finally:
        await _runtime.shutdown_neurocomment_runtime("listener-1")

    assert _runtime._SWEEP_TASK is None


@pytest.mark.asyncio
async def test_reconcile_with_no_channels_does_not_start_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_listener(monkeypatch, _ListenerSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")

    assert _runtime._SWEEP_TASK is None


@pytest.mark.asyncio
async def test_sweep_one_channel_fault_does_not_abort_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A per-channel bookkeeping fault (not the read, which _sweep_channel already
    # guards) must not abort the remaining channels of the pass.
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await link_channel_to_campaign(campaign.campaign_id, "@b")

    attempts: list[str] = []

    async def flaky(channel: str, _comments: object, _now: object) -> None:
        attempts.append(channel)
        if len(attempts) == 1:
            msg = "bookkeeping boom"
            raise RuntimeError(msg)

    monkeypatch.setattr("services.neurocomment._sweep._sweep_channel", flaky)

    await _runtime._sweep_once()  # first channel raises; second must still be swept

    assert len(attempts) == 2  # both channels processed despite the fault


async def _campaign_with_posted_comments(channel: str, msg_ids: list[int]) -> None:
    """Active campaign on ``channel`` with one ``posted`` comment per ``msg_ids`` entry."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, channel)
    await create_account(AccountCreate(account_id="acc-1", label="acc-1", session_name="acc-1"))
    for post_id, msg_id in enumerate(msg_ids, start=1):
        await claim_comment(channel, post_id, campaign.campaign_id, "acc-1")
        await mark_comment_posted(channel, post_id, comment_text="x", comment_msg_id=msg_id)


@pytest.mark.asyncio
async def test_sweep_trips_backoff_when_deletions_reach_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 2)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    async def fake_read(_account_id: str, action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        # Two of the three comments have vanished — at the threshold.
        gone = [mid for mid in action.message_ids if mid in (101, 102)]
        return CheckMessagesAliveResult(missing_ids=gone)

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_sweep_marks_deleted_comments_and_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even below the back-off threshold, vanished comments are stamped + logged once."""
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 5)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    async def fake_read(_account_id: str, action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        return CheckMessagesAliveResult(
            missing_ids=[mid for mid in action.message_ids if mid == 102]
        )

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()

    gone = await fetch_comment("@a", 2)  # post_id 2 → comment_msg_id 102
    live = await fetch_comment("@a", 1)  # post_id 1 → comment_msg_id 101
    assert gone is not None
    assert gone.deleted_at is not None
    assert live is not None
    assert live.deleted_at is None
    logs = await list_recent_logs(limit=50)
    deleted_logs = [entry for entry in logs if entry.event == "neurocomment_comment_deleted"]
    assert len(deleted_logs) == 1
    assert deleted_logs[0].extra["count"] == 1

    # Idempotent: a second sweep over the same window neither re-marks nor re-logs.
    await _runtime._sweep_once()
    again = [
        e for e in await list_recent_logs(limit=50) if e.event == "neurocomment_comment_deleted"
    ]
    assert len(again) == 1


@pytest.mark.asyncio
async def test_sweep_below_threshold_does_not_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 3)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    async def fake_read(_account_id: str, _action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        return CheckMessagesAliveResult(missing_ids=[101])  # one gone, below threshold 3

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_sweep_read_failure_does_not_trip_or_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 1)
    await _campaign_with_posted_comments("@a", [101, 102])

    async def boom(_account_id: str, _action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        msg = "read failed"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.neurocomment._seams.execute_read", boom)

    await _runtime._sweep_once()  # one channel's read failure must not abort the sweep

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_sweep_read_failure_logs_the_wrapped_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every gateway failure arrives as TelegramReadError, so the class name is useless."""
    await _campaign_with_posted_comments("@a", [101])

    async def flooded(_account_id: str, _action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        reason = "FloodWait(42s)"
        raise TelegramReadError(reason, kind="flood_wait", seconds=42)

    monkeypatch.setattr("services.neurocomment._seams.execute_read", flooded)

    await _runtime._sweep_once()

    logged = next(
        entry
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_sweep_read_failed"
    )
    assert logged.extra["reason"] == "FloodWait(42s)"
    assert logged.extra["kind"] == "flood_wait"


@pytest.mark.asyncio
async def test_sweep_disabled_when_interval_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0.0)
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    _patch_listener(monkeypatch, _ListenerSpy())
    _patch_execute(monkeypatch, _ExecuteSpy())

    await _runtime.reconcile_neurocomment_runtime("listener-1")
    try:
        assert _runtime._SWEEP_TASK is None  # sweep disabled by config
    finally:
        await _runtime.shutdown_neurocomment_runtime("listener-1")


@pytest.mark.asyncio
async def test_sweep_does_not_re_escalate_while_cooled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "channel_backoff_min_deletions", 2)
    await _campaign_with_posted_comments("@a", [101, 102, 103])

    reads = 0

    async def fake_read(_account_id: str, action: CheckMessagesAlive) -> CheckMessagesAliveResult:
        nonlocal reads
        reads += 1
        return CheckMessagesAliveResult(missing_ids=list(action.message_ids))  # all gone

    monkeypatch.setattr("services.neurocomment._seams.execute_read", fake_read)

    await _runtime._sweep_once()  # trips once
    await _runtime._sweep_once()  # already cooled → skipped: no re-read, no re-escalation

    assert _state.channel_in_backoff("@a", datetime.now(UTC)) is True
    assert _state._CHANNEL_TRIPS["@a"] == 1  # escalated exactly once, not per sweep
    assert reads == 1  # the second sweep skipped the gateway read entirely


# --------------------------------------------------------------------------- #
# Retention prune: rides the sweep tick, self-gated on its own interval.
# --------------------------------------------------------------------------- #


def _patch_purge(monkeypatch: pytest.MonkeyPatch, removed: int) -> list[str]:
    """Replace the repository purge with a recorder; returns the cutoffs it was passed."""
    cutoffs: list[str] = []

    async def fake_purge(cutoff: str) -> int:
        cutoffs.append(cutoff)
        return removed

    monkeypatch.setattr(_sweep, "purge_neurocomment_history_older_than", fake_purge)
    return cutoffs


@pytest.fixture(autouse=True)
def _fresh_prune_clock() -> None:
    # The "last prune ran at" stamp is module-level, so every test starts due.
    _sweep.reset_prune_clock()


@pytest.mark.asyncio
async def test_prune_runs_once_per_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "retention_days", 90.0)
    monkeypatch.setattr(settings.neurocomment, "retention_prune_interval_hours", 24.0)
    cutoffs = _patch_purge(monkeypatch, removed=0)

    now = datetime.now(UTC)
    await _sweep._prune_history_if_due(now)  # never ran → due
    await _sweep._prune_history_if_due(now + timedelta(hours=1))  # a 5-min tick: too soon
    await _sweep._prune_history_if_due(now + timedelta(hours=25))  # interval elapsed → due

    assert len(cutoffs) == 2  # not once per sweep tick
    assert cutoffs[0] == (now - timedelta(days=90)).isoformat()  # cutoff trails by the window


@pytest.mark.asyncio
async def test_prune_skipped_entirely_when_retention_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "retention_days", 0.0)  # keep forever
    cutoffs = _patch_purge(monkeypatch, removed=5)

    await _sweep._prune_history_if_due(datetime.now(UTC))

    assert cutoffs == []


@pytest.mark.asyncio
async def test_prune_logs_only_when_rows_were_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "retention_days", 90.0)
    _patch_purge(monkeypatch, removed=0)
    await _sweep._prune_history_if_due(datetime.now(UTC))

    # A no-op purge stays silent, mirroring ``neurocomment_comment_deleted``.
    assert await _retention_log_entries() == []

    _sweep.reset_prune_clock()
    _patch_purge(monkeypatch, removed=7)
    await _sweep._prune_history_if_due(datetime.now(UTC))

    entries = await _retention_log_entries()
    assert len(entries) == 1
    assert entries[0].extra["removed"] == 7


@pytest.mark.asyncio
async def test_prune_failure_is_logged_and_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "retention_days", 90.0)

    async def boom(_cutoff: str) -> int:
        msg = "purge boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(_sweep, "purge_neurocomment_history_older_than", boom)

    await _sweep._prune_history_if_due(datetime.now(UTC))  # must not raise

    logs = await list_recent_logs(limit=50)
    assert [e for e in logs if e.event == "neurocomment_retention_purge_failed"]
    assert await _retention_log_entries() == []


@pytest.mark.asyncio
async def test_prune_failure_does_not_re_enter_the_purge_on_the_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clock is stamped BEFORE the purge, so a failing prune waits out its interval.

    Stamped after the purge instead, a persistently failing prune would never record a run
    and would re-scan the append-only tables (and re-log the WARNING) on every ~5-minute
    sweep tick forever. The existing failure test only proves one call does not raise.
    """
    monkeypatch.setattr(settings.neurocomment, "retention_days", 90.0)
    monkeypatch.setattr(settings.neurocomment, "retention_prune_interval_hours", 24.0)
    calls = 0

    async def boom(_cutoff: str) -> int:
        nonlocal calls
        calls += 1
        msg = "purge boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(_sweep, "purge_neurocomment_history_older_than", boom)

    now = datetime.now(UTC)
    await _sweep._prune_history_if_due(now)
    await _sweep._prune_history_if_due(now + timedelta(minutes=5))  # the next sweep tick

    assert calls == 1  # the failure still counts as "ran", so the interval gates the retry


def _backdate_joins(hours: float) -> None:
    """Push every join-log row ``hours`` into the past (``record_join`` always stamps now)."""
    stamp = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_join_log SET joined_at = ?",
            (stamp,),
        )


@pytest.mark.asyncio
async def test_prune_never_reaches_inside_the_rolling_24h_join_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sub-day ``retention_days`` must not purge joins the anti-freeze cap still counts.

    ``retention_days`` is a float, so ``NEUROCOMMENT__RETENTION_DAYS=0.5`` is valid config.
    The join log is only ballast *outside* 24h: inside it, it backs the rolling-24h count
    ``_at_join_cap`` reads for the #270 cap. A 12h cutoff deletes rows that count, the cap
    under-counts, and the account over-joins into a Telegram freeze — so the cutoff is
    floored at one day. Runs the real repository purge, not a recorder.
    """
    monkeypatch.setattr(settings.neurocomment, "retention_days", 0.5)
    await record_join("acc-1")
    _backdate_joins(18)  # inside the 24h window, but older than a 0.5-day cutoff

    await _sweep._prune_history_if_due(datetime.now(UTC))

    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert await count_account_joins_since("acc-1", since) == 1


@pytest.mark.asyncio
async def test_sweep_loop_prunes_even_when_the_deletion_pass_faults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prune rides the loop behind its own guard, so neither half aborts the other."""
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0.01)
    monkeypatch.setattr(settings.neurocomment, "retention_days", 90.0)
    pruned = asyncio.Event()

    async def failing_pass() -> None:
        msg = "sweep boom"
        raise RuntimeError(msg)

    async def fake_purge(_cutoff: str) -> int:
        pruned.set()
        return 0

    monkeypatch.setattr(_sweep, "_sweep_once", failing_pass)
    monkeypatch.setattr(_sweep, "purge_neurocomment_history_older_than", fake_purge)

    task = asyncio.create_task(_sweep._sweep_loop())
    try:
        await asyncio.wait_for(pruned.wait(), timeout=5.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_sweep_loop_survives_a_fault_in_any_piggybacked_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every pass rides this tick behind the loop's guard, not only the deletion pass.

    Each review guards its own first bulk read and nothing after it, so a locked SQLite, a
    malformed timestamp or the live Telegram RPC ``deactivate_channel`` reaches unwound
    into the loop body and ended the task for the rest of the process lifetime — silently,
    since the handle carries no done-callback — taking all four lifecycle rules with it.
    """
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0.01)
    reviewed = asyncio.Event()

    async def failing_deletion_pass() -> None:
        msg = "deletion boom"
        raise RuntimeError(msg)

    async def failing_join_review(_now: datetime) -> None:
        msg = "join review boom"
        raise RuntimeError(msg)

    async def record_rejoin_review(_now: datetime) -> None:
        reviewed.set()

    monkeypatch.setattr(_sweep, "_sweep_once", failing_deletion_pass)
    monkeypatch.setattr(_sweep, "_review_join_requests", failing_join_review)
    monkeypatch.setattr(_rejoin, "review_access_lost", record_rejoin_review)

    task = asyncio.create_task(_sweep._sweep_loop())
    try:
        # The last pass of the tick still runs, so neither fault aborted its siblings...
        await asyncio.wait_for(reviewed.wait(), timeout=5.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ...and each fault named itself, so a dead pass can never be silent.
    failed = {
        entry.extra.get("pass")
        for entry in await list_recent_logs(limit=50)
        if entry.event == "neurocomment_sweep_failed"
    }
    assert failed == {"deletion", "join_requests"}


async def _retention_log_entries() -> list[LogEntry]:
    logs = await list_recent_logs(limit=50)
    return [entry for entry in logs if entry.event == "neurocomment_retention_purged"]


# --------------------------------------------------------------------------- #
# Join-request review: rides the same tick, because onboarding has no timer.
# --------------------------------------------------------------------------- #


async def _pending_campaign(*accounts: str, ready: str | None = None) -> str:
    """A campaign on @gated where every account has an outstanding join request.

    ``ready`` names one account whose pair is instead joined and comment-able.
    """
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@gated")
    for account_id in accounts:
        await create_account(AccountCreate(account_id=account_id, session_name=account_id))
        # Assigned, so the give-up rule can see who actually serves the channel.
        await assign_account_to_campaign(campaign.campaign_id, account_id)
        is_ready = account_id == ready
        await upsert_readiness(
            account_id, "@gated", joined=is_ready, captcha_passed=is_ready, ready=is_ready
        )
        if not is_ready:
            await stamp_join_request(account_id, "@gated")
    return campaign.campaign_id


async def _gated_is_active(campaign_id: str) -> bool:
    links = (await list_campaign_channels(campaign_id)).links
    return any(link.channel == "@gated" and link.active for link in links)


@pytest.mark.asyncio
async def test_review_drops_a_channel_nobody_approved_within_the_budget() -> None:
    campaign_id = await _pending_campaign("acc-1", "acc-2")

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=49))

    assert await _gated_is_active(campaign_id) is False
    expired = [
        e
        for e in await list_recent_logs(limit=50)
        if e.event == "neurocomment_join_request_expired"
    ]
    assert [
        (e.level, e.extra.get("channel"), e.extra.get("pending_accounts")) for e in expired
    ] == [("WARNING", "@gated", 2)]


@pytest.mark.asyncio
async def test_review_keeps_a_channel_that_has_a_ready_pair() -> None:
    """One stubborn account must not kill a channel the others comment in fine."""
    campaign_id = await _pending_campaign("acc-1", "acc-2", ready="acc-2")

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=200))

    assert await _gated_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_review_keeps_a_channel_whose_fleet_has_not_all_tried_it_yet() -> None:
    """A serving account with NO readiness row was never tried here, not tried and failed.

    Onboarding reaches a fleet slowly, so counting only the rows that exist let one expired
    request drop a channel the campaign's other accounts had never touched. Same coverage
    rule as ``bans._unlink_channel_if_no_account_left`` and the access-loss review.
    """
    campaign_id = await _pending_campaign("acc-1")
    await create_account(AccountCreate(account_id="acc-2", session_name="acc-2"))
    await assign_account_to_campaign(campaign_id, "acc-2")  # serving, never onboarded

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=200))

    assert await _gated_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_review_leaves_a_channel_alone_inside_the_budget() -> None:
    campaign_id = await _pending_campaign("acc-1")

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=47))

    assert await _gated_is_active(campaign_id) is True


@pytest.mark.asyncio
async def test_review_triggers_onboarding_once_a_retry_falls_due(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review pokes onboarding when the 24h window lapses.

    Nothing else would re-send the second request: onboarding runs on operator actions
    and boot only, so a due retry would sit there until the next Start.
    """
    await _pending_campaign("acc-1")
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=25))

    assert len(triggered) == 1


@pytest.mark.asyncio
async def test_review_does_not_re_trigger_onboarding_past_the_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _pending_campaign("acc-1")
    await stamp_join_request("acc-1", "@gated")  # second and last request sent
    triggered: list[object] = []
    monkeypatch.setattr(_runtime, "_ensure_onboarding_running", triggered.append)

    await _sweep._review_join_requests(datetime.now(UTC) + timedelta(hours=25))

    assert triggered == []


@pytest.mark.asyncio
async def test_review_failure_is_logged_and_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom() -> None:
        msg = "read boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(_sweep, "list_pending_join_readiness", boom)

    await _sweep._review_join_requests(datetime.now(UTC))  # must not raise

    logs = await list_recent_logs(limit=50)
    assert [e for e in logs if e.event == "neurocomment_join_request_review_failed"]


# --------------------------------------------------------------------------- #
# Stale-claim reclaim: rides this tick because startup was its only trigger. A dead
# worker's row stays 'claimed', which ``_quota`` spends as a day slot until a restart.
# --------------------------------------------------------------------------- #


async def _claim_aged(minutes: float) -> None:
    """Claim @a post 1 for acc-1, then age the row ``minutes`` into the past."""
    campaign = await create_campaign(CampaignCreate(name="A", prompt="p", status="active"))
    await link_channel_to_campaign(campaign.campaign_id, "@a")
    await create_account(AccountCreate(account_id="acc-1", session_name="acc-1"))
    assert await claim_comment("@a", 1, campaign.campaign_id, "acc-1") is True
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE neurocomment_comments SET created_at = ? WHERE post_id = 1",
            ((datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(),),
        )


async def _spent_day_slots() -> int:
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return await count_account_channel_comments_since("acc-1", "@a", since)


async def _run_until_second_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the real loop until tick two begins — so every pass of tick one has run."""
    monkeypatch.setattr(settings.neurocomment, "deletion_sweep_interval_seconds", 0.01)
    second = asyncio.Event()
    ticks: list[int] = []

    async def counted() -> None:
        ticks.append(1)
        if len(ticks) == 2:
            second.set()

    monkeypatch.setattr(_sweep, "_sweep_once", counted)
    # Started through the runtime, not by hand: the loop retires the moment it is not the
    # registered sweep task, so a hand-built one would quit after tick one.
    _runtime._ensure_sweep_running()
    try:
        await asyncio.wait_for(second.wait(), timeout=5.0)
    finally:
        await _runtime._stop_sweep()  # bounded cancel, and clears the handle


@pytest.mark.asyncio
async def test_sweep_tick_reclaims_a_stranded_claim_and_frees_its_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim its worker died holding is failed on the tick, not only on the next boot."""
    await _claim_aged(60)  # well past the 900s cutoff
    assert await _spent_day_slots() == 1

    await _run_until_second_tick(monkeypatch)

    row = await fetch_comment("@a", 1)
    assert row is not None
    assert row.status == "failed"  # terminal, so the idempotency gate survives the reclaim
    assert await _spent_day_slots() == 0  # quota counts only claimed/posted


@pytest.mark.asyncio
async def test_sweep_tick_leaves_a_claim_that_is_still_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never reclaim out from under a live worker: ~4 min of generate + delay + send."""
    await _claim_aged(0)

    await _run_until_second_tick(monkeypatch)

    row = await fetch_comment("@a", 1)
    assert row is not None
    assert row.status == "claimed"
    assert await _spent_day_slots() == 1
