"""The ownership registry, exercised from BOTH sides of a real campaign start.

``services._account_owner`` had one writer until now. Warming claimed and released,
warming's ``assert_not_neuroshilling`` and neurocomment's ``busy_neuroshilling``
selection branch read — and neither could ever fire, because nothing on the
neuroshilling side wrote a claim. This file is the proof that both do now, driven
through ``_runtime.start_campaign`` rather than through a hand-planted claim: a test
that plants one asserts the branch, not the wiring.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.db import (
    assign_account_to_campaign,
    configure_database,
    create_account,
    create_campaign,
    fetch_warming_state,
    link_channel_to_campaign,
)
from core.repositories import neuroshilling as repository
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate, NeurocommentSettings
from schemas.telegram_actions import ResolveChatResult
from schemas.warming import StartWarmingRequest
from services import _account_owner, warming
from services.neurocomment import _gates as nc_gates
from services.neurocomment import _state as nc_state
from services.neurocomment import engine as nc_engine
from services.neuroshilling import _runtime, _seams, _state, _steps, _telegram
from services.neuroshilling import engine as ns_engine
from services.neuroshilling.campaigns import NeuroshillingConflictError
from services.warming import _runtime as warming_runtime
from services.warming._exclusion import AccountUnavailableError
from tests.services.neuroshilling.helpers import seed_campaign, sent

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.telegram_actions import ActionResult, TelegramAction

# Long enough for the other coroutine to reach its gate, short enough that the one wait
# the lock makes unreachable does not slow the suite down.
_GATE_SECONDS = 0.3


@pytest.fixture(autouse=True)
def isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A fresh database, empty process state, and no real sleeping or dispatching."""
    configure_database(tmp_path / "telebuba.db")
    _reset()

    async def _execute(_account_id: str, _action: TelegramAction) -> ActionResult:
        return sent()

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "joined"

    async def _nothing(*_args: object) -> None:
        return None

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_seams, "sleep", _nothing)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    monkeypatch.setattr("services.pacing.await_send_slot", _nothing)
    yield
    _reset()


def _reset() -> None:
    _account_owner.reset_for_tests()
    _state.reset_for_tests()
    _steps.reset_for_tests()
    _runtime.reset_for_tests()
    # And the lifecycle locks, which no other fixture empties. An ``asyncio.Lock``
    # belongs to the loop that first WAITED on it, and this file is where two coroutines
    # contend on one: a lock left behind by an earlier test's loop answers the second
    # waiter with ``RuntimeError`` instead of blocking it, which reads as the refusal
    # under test never happening. Same failure the root conftest resets pacing and the
    # join mutexes for, scoped here because this is the only file that contends.
    warming_runtime._ACCOUNT_LOCKS.clear()


@pytest.fixture
def live_run(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """Hold the run task open, so the claim is still there when the test looks.

    A run that plays two zero-delay steps against a stub gateway finishes during the
    very first ``await`` after Start returns, and hands its accounts back — so a test
    that asserted the hold straight afterwards would be asserting a coin flip.
    """
    running = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    monkeypatch.setattr(ns_engine, "run_campaign", _never_ends)
    return running


@pytest.mark.usefixtures("live_run")
@pytest.mark.asyncio
async def test_warming_refuses_an_account_a_running_campaign_holds() -> None:
    """The half that was dead code: warming's guard over an always-empty registry."""
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)

    with pytest.raises(AccountUnavailableError) as refusal:
        await warming.start_warming(StartWarmingRequest(account_id="acc-1"))

    assert refusal.value.code == "account_busy_neuroshilling"
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
@pytest.mark.usefixtures("live_run")
async def test_neurocomment_selection_skips_an_account_a_running_campaign_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selection runs on EVERY post, so a campaign taken between two of them is seen."""
    monkeypatch.setattr(nc_state, "in_cooldown", lambda *_args: False)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)

    reason = nc_gates._account_block_reason(
        "acc-1",
        "@chan",
        1,
        datetime.now(UTC),
        _empty_pool(),
    )

    assert reason == "busy_neuroshilling"


def _empty_pool() -> nc_engine._SelectionPool:
    """A pool that knows nothing, so only the registry read can produce a verdict."""
    return nc_engine._SelectionPool(
        accounts={},
        readiness={},
        states={},
        spam={},
        fingerprints={},
        hourly_counts={},
        daily_counts={},
        overrides={},
        limits=NeurocommentSettings(
            max_comments_per_hour=5,
            max_comments_per_channel_per_day=3,
            reply_delay_min_seconds=0,
            reply_delay_max_seconds=1,
            min_trust_score=0,
            updated_at="2026-01-01T00:00:00+00:00",
        ),
    )


@pytest.mark.asyncio
async def test_a_start_refuses_an_account_warming_is_driving() -> None:
    """The reciprocal, and the reason the claim loop is all-or-nothing."""
    seeded = await seed_campaign()
    _account_owner.try_claim("acc-2", "warming", "run-w")

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "account_busy"
    assert _account_owner.owner_of("acc-1") is None


@pytest.mark.asyncio
async def test_a_start_refuses_an_account_serving_a_neurocomment_campaign() -> None:
    """Neurocomment never writes the registry, so this one is asked of the DATABASE."""
    seeded = await seed_campaign()
    campaign = await create_campaign(CampaignCreate(name="Comments", prompt="mention X"))
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "account_busy"
    assert _account_owner.owner_of("acc-2") is None


@pytest.mark.asyncio
async def test_a_finished_run_hands_its_accounts_back() -> None:
    """A hold that outlived its run would take the account off the fleet until a restart."""
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)
    await _runtime.stop_campaign(seeded.campaign_id)

    assert _account_owner.owners() == {}


@pytest.mark.usefixtures("live_run")
@pytest.mark.asyncio
async def test_an_account_may_be_assigned_to_two_campaigns_but_held_by_one() -> None:
    """Assignment is a row and nothing refuses it; holding is what the registry decides."""
    first = await seed_campaign()
    second = await seed_campaign(accounts=("acc-1", "acc-2"), targets="@beta")
    assert {item.account_id for item in await repository.list_campaign_accounts(second.campaign_id)}

    await _runtime.start_campaign(first.campaign_id)

    with pytest.raises(NeuroshillingConflictError):
        await _runtime.start_campaign(second.campaign_id)


@pytest.mark.usefixtures("live_run")
@pytest.mark.asyncio
async def test_a_warming_start_cannot_evict_a_campaign_claiming_inside_its_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Warming's refusal and its eviction are several awaits apart, not one stretch.

    ``_spawn_runtime_task`` publishes with ``_account_owner.take_over``, which cannot be
    told no, and between ``assert_not_neuroshilling`` above it and that write
    ``start_warming`` awaits a readiness verdict and a bounded wait for the previous task
    to unwind — seconds, not instructions. What keeps a campaign out of that window is
    the per-account lifecycle lock: warming holds it across the whole of Start, and
    ``_claim_accounts`` enters it for every roster account before it reads or claims
    anything. Take that lock away and both sides drive one session.

    Forced with a gate, because a ``gather`` cannot produce this order: the campaign has
    to arrive after warming's refusal and before its spawn. The campaign's own wait is
    what expires — it is parked on the lock warming is holding — and that expiry is the
    shape of the proof rather than a hedge.
    """
    seeded = await seed_campaign()
    refusal_passed = asyncio.Event()
    campaign_settled = asyncio.Event()

    async def _gate_after_the_refusal(_account_id: str, _account: object) -> None:
        refusal_passed.set()
        with suppress(TimeoutError):
            await asyncio.wait_for(campaign_settled.wait(), timeout=_GATE_SECONDS)

    async def _parked_loop(_account_id: str, *, run_id: str | None = None) -> None:  # noqa: ARG001
        await asyncio.Event().wait()

    monkeypatch.setattr(warming_runtime, "_enforce_start_readiness", _gate_after_the_refusal)
    monkeypatch.setattr(warming_runtime, "_warming_loop", _parked_loop)

    async def _claim_inside_the_window() -> None:
        await asyncio.wait_for(refusal_passed.wait(), timeout=_GATE_SECONDS)
        try:
            await _runtime.start_campaign(seeded.campaign_id)
        finally:
            campaign_settled.set()

    outcomes = await asyncio.gather(
        warming.start_warming(StartWarmingRequest(account_id="acc-1")),
        _claim_inside_the_window(),
        return_exceptions=True,
    )

    refusals = [item for item in outcomes if isinstance(item, BaseException)]
    assert [getattr(item, "code", None) for item in refusals] == ["account_busy"], outcomes
    assert _account_owner.holder_of("acc-1") == (await warming_state_run_id())
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    assert campaign.status == "idle"
    await _cancel_warming_task("acc-1")


async def warming_state_run_id(account_id: str = "acc-1") -> str | None:
    """The generation warming published for ``account_id``, as its own row records it."""
    record = await fetch_warming_state(account_id)
    return None if record is None else record.run_id


async def _cancel_warming_task(account_id: str) -> None:
    """Unwind the parked warming loop, so its claim leaves the registry with it."""
    task = warming_runtime._RUNTIME.pop(account_id, None)
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_an_unknown_account_is_not_claimed_by_anybody() -> None:
    """Guards the fixture itself: the registry starts empty in every case above."""
    await create_account(AccountCreate(account_id="lonely", label="L", session_name="lonely"))

    assert _account_owner.owner_of("lonely") is None
