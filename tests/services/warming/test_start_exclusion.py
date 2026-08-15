"""Warming's half of the mutual exclusion with neurocomment's read traffic.

``test_runtime_start_stop`` already pins the listener guard. This module covers the two
holes beside it: a channel-discovery run (which may only START while the listener is
stopped — exactly the state the listener guard passes) and a live flood cooldown either
runtime recorded.

Both the refusals AND the still-allowed cases are pinned. A guard that parks warming on
a channel-scoped slow-mode cooldown, or for a discovery run reading with a different
account, leaves the operator unable to warm anything — which is the same outage as an
account with two paced streams on it, only slower to notice.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.db import create_account
from schemas.accounts import AccountCreate
from schemas.warming import StartWarmingRequest
from services import warming
from services.neurocomment import _discovery_state, _state
from services.warming import _runtime
from services.warming._exclusion import COOLING_CODE, DISCOVERY_CODE
from tests.services.warming._support import _fake_loop, _seed_ready_account, _set_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

_ACCOUNT = "acc-1"
_CAMPAIGN = "camp-1"


@pytest.fixture(autouse=True)
def _isolate_neurocomment_state() -> Iterator[None]:
    """Both facts warming now reads live in neurocomment module globals."""
    _discovery_state.reset_for_tests()
    _state.reset_for_tests()
    yield
    _discovery_state.reset_for_tests()
    _state.reset_for_tests()


async def _ready_account(monkeypatch: pytest.MonkeyPatch, account_id: str = _ACCOUNT) -> None:
    """An account warming will start, with readiness enforcement OFF.

    Right for the two hard exclusions, which the operator cannot overrule.
    """
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id=account_id))
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=False)


async def _guarded_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """An account that passes readiness WITH enforcement on.

    The cooldown refusal lives behind that switch, so a test that leaves it off proves
    nothing at all — and the still-allowed cases have to clear the other readiness
    reasons or they would pass on the wrong refusal.
    """
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await _seed_ready_account(_ACCOUNT)
    await _set_settings(chat=False, reactions=False, key="", enforce_readiness=True)


def _claim_discovery(account_id: str, campaign_id: str = _CAMPAIGN) -> None:
    """Put a discovery run's in-memory claim on ``account_id``, as a real start does."""
    assert _discovery_state.try_reserve(campaign_id, account_id) is None


async def _stop_task() -> None:
    task = warming._RUNTIME.pop(_ACCOUNT, None)
    if task is not None:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_start_warming_refuses_while_discovery_reads_with_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hole the listener guard cannot see: discovery starts only when it is paused."""
    await _ready_account(monkeypatch)
    _claim_discovery(_ACCOUNT)

    with pytest.raises(warming.AccountUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert refused.value.code == DISCOVERY_CODE
    assert _ACCOUNT not in warming._RUNTIME


@pytest.mark.asyncio
async def test_a_discovery_run_on_another_account_does_not_block_warming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim is per account, not a fleet-wide "discovery is happening" flag."""
    await _ready_account(monkeypatch)
    _claim_discovery("acc-other")

    started = await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert started.state == "active"
    await _stop_task()


@pytest.mark.asyncio
async def test_a_finished_discovery_run_releases_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim is the run's lifetime, so warming must be startable the moment it ends."""

    async def _instant() -> None:
        return None

    await _ready_account(monkeypatch)
    _claim_discovery(_ACCOUNT)
    _discovery_state.spawn(_CAMPAIGN, _instant())
    # Two hops: one for the task body, one for the done callback that forgets the claim.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    started = await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert started.state == "active"
    await _stop_task()


@pytest.mark.asyncio
async def test_start_warming_refuses_an_account_serving_a_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery and the comment engine park a flooded account here; warming reads it now."""
    await _guarded_account(monkeypatch)
    await _state.set_cooldown(_ACCOUNT, datetime.now(UTC) + timedelta(hours=1))

    with pytest.raises(warming.AccountUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert refused.value.code == COOLING_CODE
    assert _ACCOUNT not in warming._RUNTIME


@pytest.mark.asyncio
async def test_the_readiness_switch_lets_the_operator_nurse_a_cooling_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cooldown obeys the escape hatch warming's own flood deadline already obeys.

    Warming's own ``flood_wait_until`` reaches Start only as a trust penalty inside
    ``evaluate_readiness``, so ``enforce_readiness=False`` overrides it. Ungated, the
    imported cooldown would have been the ONE health verdict with no override — for a
    window Telegram chose, hours long on a premium wait, surviving restarts through
    ``hydrate_cooldowns``. Nursing a just-flooded account back is what warming is for.
    """
    await _ready_account(monkeypatch)
    await _state.set_cooldown(_ACCOUNT, datetime.now(UTC) + timedelta(hours=1))

    started = await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert started.state == "active"
    await _stop_task()


@pytest.mark.asyncio
async def test_the_readiness_switch_does_not_unlock_a_live_discovery_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two paced streams on one session is not a health opinion to overrule."""
    await _ready_account(monkeypatch)
    _claim_discovery(_ACCOUNT)

    with pytest.raises(warming.AccountUnavailableError) as refused:
        await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert refused.value.code == DISCOVERY_CODE


@pytest.mark.asyncio
async def test_an_expired_cooldown_does_not_block_warming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cooldown is only ever removed by expiry, so a lapsed one must not park anything."""
    await _guarded_account(monkeypatch)
    await _state.set_cooldown(_ACCOUNT, datetime.now(UTC) - timedelta(seconds=1))

    started = await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert started.state == "active"
    await _stop_task()


@pytest.mark.asyncio
async def test_a_cooldown_on_another_account_does_not_block_warming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _guarded_account(monkeypatch)
    await _state.set_cooldown("acc-other", datetime.now(UTC) + timedelta(hours=1))

    started = await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert started.state == "active"
    await _stop_task()


@pytest.mark.asyncio
async def test_a_channel_scoped_cooldown_does_not_park_warming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow mode in one chat says nothing about the account's own traffic.

    ``set_cooldown`` scopes to a channel precisely so it does not park the account
    everywhere; refusing warming on it would let one talkative discussion group stop the
    operator warming that account at all.
    """
    await _guarded_account(monkeypatch)
    await _state.set_cooldown(_ACCOUNT, datetime.now(UTC) + timedelta(hours=1), channel="@chat")

    started = await warming.start_warming(StartWarmingRequest(account_id=_ACCOUNT))

    assert started.state == "active"
    await _stop_task()
