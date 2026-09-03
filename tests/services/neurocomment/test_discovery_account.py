"""Which accounts a discovery run may use, and the start refusals that follow.

Split out of ``test_discovery_search.py`` (700-line test cap): that file covers the
source fan-out and the merge, this one covers the per-pick account check plus the
statuses ``start_discovery`` answers instead of raising.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from core.config import settings
from core.db import create_account, upsert_warming_state
from core.repositories.neurocomment import get_listener_running, set_listener_running
from schemas.accounts import AccountCreate
from schemas.neurocomment_discovery import DiscoverySearchStageResult
from schemas.warming import StartWarmingRequest, WarmingStateWrite
from services.neurocomment import _discovery_run, _discovery_state, _seams, _state
from services.warming import _runtime
from tests.services.neurocomment.discovery_support import (
    LISTENER_ID,
    ReadRecorder,
    drain_discovery,
    matches,
    new_campaign,
    search_request,
    seed_account,
    seed_listener,
    start_run,
)

pytestmark = pytest.mark.usefixtures("isolate_discovery")


@pytest.mark.asyncio
async def test_start_discovery_spawns_and_reports_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches(("alpha1", "A", None))))
    await seed_listener()
    campaign_id = await new_campaign()

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_start_discovery_is_single_flighted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _slow(_account_id: str, _action: object) -> object:
        await asyncio.sleep(5)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _slow)
    await seed_listener()
    campaign_id = await new_campaign()

    first = await start_run(campaign_id, search_request())
    second = await start_run(campaign_id, search_request())

    assert first.status == "started"
    assert second.status == "already_running"


@pytest.mark.asyncio
async def test_start_discovery_with_an_unknown_pick_refuses() -> None:
    """The picked id names no account on this dashboard."""
    campaign_id = await new_campaign()

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "no_account"
    assert outcome.refused_account_id == LISTENER_ID


@pytest.mark.asyncio
async def test_a_pick_that_was_never_signed_in_is_refused() -> None:
    """An account row with no session has nothing to read with."""
    await create_account(AccountCreate(account_id="acc-fresh", label="fresh"))
    campaign_id = await new_campaign()

    outcome = await start_run(campaign_id, search_request(account_ids=["acc-fresh"]))

    assert outcome.status == "no_account"
    assert outcome.refused_account_id == "acc-fresh"


@pytest.mark.asyncio
async def test_a_warming_account_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warming's freeze avoidance assumes it owns its accounts' traffic."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await upsert_warming_state(WarmingStateWrite(account_id=LISTENER_ID, state="active"))

    refused = await start_run(campaign_id, search_request())

    # busy, not cooling: the account is healthy, its session is just taken.
    assert refused.status == "account_busy"
    assert refused.refused_account_id == LISTENER_ID


@pytest.mark.asyncio
async def test_a_running_listener_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listener is preferred for being read-only, but a running one holds the session.

    Discovery would layer minutes of paced keyword reads plus up to 100 probes onto the
    account the live listener is reading with — the same mutual exclusion warming gets.
    """
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await set_listener_running(running=True)

    refused = await start_run(campaign_id, search_request())

    assert refused.status == "account_busy"


@pytest.mark.asyncio
async def test_a_running_listener_does_not_block_a_serving_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard is about sharing one session, not about the listener existing."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    await seed_account("acc-serving")
    campaign_id = await new_campaign()
    await set_listener_running(running=True)

    outcome = await start_run(campaign_id, search_request(account_ids=["acc-serving"]))

    assert outcome.status == "started"


@pytest.mark.asyncio
async def test_start_discovery_refuses_a_cooling_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Searching on a rate-limited account would deepen the very limit it is serving."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    await _state.set_cooldown(LISTENER_ID, datetime.now(UTC) + timedelta(hours=1))

    outcome = await start_run(campaign_id, search_request())

    assert outcome.status == "account_cooling"


@pytest.mark.asyncio
async def test_start_discovery_refuses_an_account_in_warming_flood_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.warming._state import _set_state  # noqa: PLC0415

    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()
    until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    await _set_state(LISTENER_ID, "flood_wait", flood_wait_until=until)

    outcome = await start_run(campaign_id, search_request())

    # ``flood_wait`` is an active warming state, so this account matches the busy
    # branch too — the Telegram-limit reason must win, which pins the check order.
    assert outcome.status == "account_cooling"


@pytest.mark.asyncio
async def test_start_discovery_honours_the_daily_search_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    first_campaign = await new_campaign()
    second_campaign = await new_campaign()

    first = await start_run(first_campaign, search_request())
    # Let it finish first: both campaigns resolve to the same listener, so an
    # overlapping start is refused for holding the account before the cap is consulted.
    await drain_discovery(first_campaign)
    second = await start_run(second_campaign, search_request())

    assert first.status == "started"
    assert second.status == "daily_limit_reached"


@pytest.mark.asyncio
async def test_daily_cap_does_not_count_a_refused_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal must not consume the operator's allowance."""
    monkeypatch.setattr(settings.neurocomment, "discovery_max_searches_per_day", 1)
    campaign_id = await new_campaign()

    refused = await start_run(campaign_id, search_request())

    assert refused.status == "no_account"
    assert _discovery_state.at_daily_search_cap() is False


async def _warmable_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """The listener account, ready for ``start_warming`` with its loop stubbed out."""
    from core.db import save_warming_settings  # noqa: PLC0415
    from services.warming import _runtime  # noqa: PLC0415

    async def _fake_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.sleep(3600)

    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )


async def _noop_reconcile(_listener: str) -> None:
    """Stub the listener start's post-commit network work; only its guard is under test."""
    return


async def _drop_warming_task(account_id: str) -> None:
    from services import warming  # noqa: PLC0415

    task = warming._RUNTIME.pop(account_id, None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_a_live_discovery_run_refuses_warming_on_the_same_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reciprocal guard, end to end through the run's real claim.

    Discovery may only start while the listener is STOPPED, which is exactly the state
    ``start_warming``'s listener check passes — so before this, the operator could stop
    the listener, start a search and then start warming on the account the search was
    reading with.
    """
    from services import warming  # noqa: PLC0415

    async def _slow(_account_id: str, _action: object) -> object:
        await asyncio.sleep(5)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _slow)
    await seed_listener()
    await _warmable_listener(monkeypatch)
    campaign_id = await new_campaign()

    started = await start_run(campaign_id, search_request())

    assert started.status == "started"
    with pytest.raises(warming.AccountUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id=LISTENER_ID))
    assert refused.value.code == "account_running_discovery"
    assert LISTENER_ID not in warming._RUNTIME


@pytest.mark.asyncio
async def test_a_warming_start_between_resolution_and_the_claim_still_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim may not be made on a health verdict that has gone stale.

    ``check_search_accounts`` answers several awaits before ``try_reserve`` runs, and
    ``start_warming`` needs only that gap to commit. So the claim is made under warming's
    own per-account lifecycle lock, re-checking warming inside it — the shape
    ``start_neurocomment`` already uses for the listener. Held open here on purpose:
    with only the check-time verdict, both starts commit and one account carries two
    paced streams.
    """
    from services import warming  # noqa: PLC0415
    from services.neurocomment import discovery as discovery_service  # noqa: PLC0415

    resolved = asyncio.Event()
    warming_committed = asyncio.Event()
    real_check = discovery_service.check_search_accounts

    async def _stalled_check(campaign_id: str, account_ids: list[str]) -> object:
        accounts = await real_check(campaign_id, account_ids)
        resolved.set()
        await warming_committed.wait()
        return accounts

    monkeypatch.setattr(discovery_service, "check_search_accounts", _stalled_check)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    await _warmable_listener(monkeypatch)
    campaign_id = await new_campaign()

    pending = asyncio.create_task(start_run(campaign_id, search_request()))
    await resolved.wait()
    started = await warming.start_warming(StartWarmingRequest(account_id=LISTENER_ID))
    warming_committed.set()
    refused = await pending

    assert started.state == "active"
    assert refused.status == "account_busy"
    await _drop_warming_task(LISTENER_ID)


@pytest.mark.asyncio
async def test_the_claim_waits_for_a_warming_start_that_is_mid_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock, not the re-check: discovery must not read state warming is still writing.

    ``start_warming`` has five awaits between its own guard and the ``_set_state("active")``
    commit. Suspended in that window it has decided to warm the account and nothing
    persistent says so yet, so a re-check alone answers "not warming" and both starts
    commit. The lock is what keeps discovery out of the window entirely: it blocks, and by
    the time it looks the commit is done.

    The previous test released warming BEFORE letting discovery through, so the two never
    overlapped and it passed with the lock replaced by a fresh unlocked one.
    """
    from services import warming  # noqa: PLC0415

    in_commit_window = asyncio.Event()
    finish_warming = asyncio.Event()
    real_fetch = _runtime.fetch_warming_state

    async def _stalled_fetch(account_id: str) -> object:
        # Called after the exclusion guard and before the state commit — the window.
        in_commit_window.set()
        await finish_warming.wait()
        return await real_fetch(account_id)

    monkeypatch.setattr(_runtime, "fetch_warming_state", _stalled_fetch)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    await _warmable_listener(monkeypatch)
    campaign_id = await new_campaign()

    warm = asyncio.create_task(warming.start_warming(StartWarmingRequest(account_id=LISTENER_ID)))
    await in_commit_window.wait()
    search = asyncio.create_task(start_run(campaign_id, search_request()))
    # Long enough for the claim to be made if nothing were holding it back: the re-check
    # it would pass through is a threaded DB read, so yielding the loop is not enough.
    await asyncio.sleep(0.1)
    assert _discovery_state.is_running(campaign_id) is False, "claimed while warming committed"

    finish_warming.set()
    started = await warm
    refused = await search

    assert started.state == "active"
    assert refused.status == "account_busy"
    await _drop_warming_task(LISTENER_ID)


@pytest.mark.asyncio
async def test_a_live_discovery_run_refuses_the_listener_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The likeliest sequence of all, because a run can only start while it is stopped.

    Operator stops the listener, searches, then presses Start again while the run is
    still going — and the post listener resolves peers and joins on the same session the
    run is paced-reading with. The generation fence cannot catch it: it is a ContextVar
    set inside the listener's task context, so a discovery task spawned from an API
    request reads ``None`` and every assertion on it passes.
    """
    from services.neurocomment import _runtime as nc_runtime  # noqa: PLC0415
    from services.neurocomment._runtime_operations import (  # noqa: PLC0415
        ListenerBusyDiscoveryError,
    )

    async def _slow(_account_id: str, _action: object) -> object:
        await asyncio.sleep(5)
        return matches()

    monkeypatch.setattr(_seams, "execute_read", _slow)
    await seed_listener()
    campaign_id = await new_campaign()

    started = await start_run(campaign_id, search_request())

    assert started.status == "started"
    with pytest.raises(ListenerBusyDiscoveryError):
        await nc_runtime.start_neurocomment(LISTENER_ID)
    assert await get_listener_running() is False


@pytest.mark.asyncio
async def test_a_listener_start_between_resolution_and_the_claim_still_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The listener half of the stale-resolution refusal, not only the warming half.

    ``check_search_accounts``'s listener check sits outside the claim lock too, so the
    account could be re-armed as the running listener in the same gap warming used.
    """
    from services.neurocomment import _runtime as nc_runtime  # noqa: PLC0415
    from services.neurocomment import discovery as discovery_service  # noqa: PLC0415

    resolved = asyncio.Event()
    listener_committed = asyncio.Event()
    real_check = discovery_service.check_search_accounts

    async def _stalled_check(campaign_id: str, account_ids: list[str]) -> object:
        accounts = await real_check(campaign_id, account_ids)
        resolved.set()
        await listener_committed.wait()
        return accounts

    monkeypatch.setattr(discovery_service, "check_search_accounts", _stalled_check)
    monkeypatch.setattr(nc_runtime, "reconcile_neurocomment_runtime", _noop_reconcile)
    monkeypatch.setattr(nc_runtime, "_ensure_onboarding_running", lambda *a, **k: None)  # noqa: ARG005
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_listener()
    campaign_id = await new_campaign()

    pending = asyncio.create_task(start_run(campaign_id, search_request()))
    await resolved.wait()
    await nc_runtime.start_neurocomment(LISTENER_ID)
    listener_committed.set()
    refused = await pending

    assert await get_listener_running() is True
    assert refused.status == "account_busy"


@pytest.mark.asyncio
async def test_another_campaign_s_start_in_the_gap_is_named_as_the_busy_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third holder the claim re-asks about: a run another campaign started in the gap.

    ``try_reserve`` still refused it, but as ``already_running`` naming no account —
    which points the operator at THIS campaign, which has no run at all.
    """
    from services.neurocomment import discovery as discovery_service  # noqa: PLC0415

    async def _hang(*_args: object, **_kwargs: object) -> DiscoverySearchStageResult:
        await asyncio.Event().wait()
        return DiscoverySearchStageResult()

    resolved = asyncio.Event()
    other_committed = asyncio.Event()
    real_check = discovery_service.check_search_accounts
    stalled: list[str] = []

    async def _stall_the_first(campaign_id: str, account_ids: list[str]) -> object:
        accounts = await real_check(campaign_id, account_ids)
        if not stalled:
            stalled.append(campaign_id)
            resolved.set()
            await other_committed.wait()
        return accounts

    monkeypatch.setattr(_discovery_run, "run_search", _hang)
    monkeypatch.setattr(discovery_service, "check_search_accounts", _stall_the_first)
    await seed_listener()
    loser = await new_campaign()
    winner = await new_campaign()

    pending = asyncio.create_task(start_run(loser, search_request()))
    await resolved.wait()
    started = await start_run(winner, search_request())
    other_committed.set()
    refused = await pending

    assert started.status == "started"
    assert (refused.status, refused.refused_account_id) == ("account_busy", LISTENER_ID)


@pytest.mark.asyncio
async def test_every_picked_account_reads_and_all_are_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run rotates over the operator's picks, and each one is held while it runs."""
    reader = ReadRecorder(search=matches())
    monkeypatch.setattr(_seams, "execute_read", reader)
    await seed_account("acc-b")
    await seed_account("acc-a")
    campaign_id = await new_campaign()

    started = await start_run(
        campaign_id, search_request(keywords=["alpha", "bravo"], account_ids=["acc-b", "acc-a"])
    )

    assert started.status == "started"
    # Both are busy for warming and the listener for as long as the run is in flight.
    assert _discovery_state.account_busy("acc-a") is True
    assert _discovery_state.account_busy("acc-b") is True
    await drain_discovery(campaign_id)
    assert set(reader.accounts) == {"acc-a", "acc-b"}
    assert _discovery_state.account_busy("acc-a") is False


@pytest.mark.asyncio
async def test_a_refusal_names_the_first_bad_pick_in_id_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked in sorted order, so which account the SPA points at is deterministic."""
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_account("acc-a")
    await seed_account("acc-c")
    campaign_id = await new_campaign()
    await _state.set_cooldown("acc-c", datetime.now(UTC) + timedelta(hours=1))

    refused = await start_run(campaign_id, search_request(account_ids=["acc-c", "acc-b", "acc-a"]))

    # ``acc-b`` is unknown and sorts before the cooling ``acc-c``; nothing was claimed.
    assert refused.status == "no_account"
    assert refused.refused_account_id == "acc-b"
    assert _discovery_state.is_running(campaign_id) is False


@pytest.mark.asyncio
async def test_the_locks_are_taken_in_id_order_whatever_the_pick_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two starts over overlapping picks must lock in ONE order, or they deadlock."""
    from services import warming  # noqa: PLC0415

    taken: list[str] = []
    real_lock = warming.account_lock

    def _recording_lock(account_id: str) -> asyncio.Lock:
        taken.append(account_id)
        return real_lock(account_id)

    monkeypatch.setattr(warming, "account_lock", _recording_lock)
    monkeypatch.setattr(_seams, "execute_read", ReadRecorder(search=matches()))
    await seed_account("acc-a")
    await seed_account("acc-b")
    campaign_id = await new_campaign()

    started = await start_run(campaign_id, search_request(account_ids=["acc-b", "acc-a"]))
    await drain_discovery(campaign_id)

    assert started.status == "started"
    # The run's own reads take the lock too, so only the start's first two matter.
    assert taken[:2] == ["acc-a", "acc-b"]
