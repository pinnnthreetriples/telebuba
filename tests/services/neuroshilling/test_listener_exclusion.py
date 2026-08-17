"""Neuroshilling and the neurocomment listener may not share one Telegram session.

The listener is not a holder in ``services._account_owner`` (see that module), so the
exclusion is a point check on each side rather than the registry doing it for both:
``_claim_accounts`` reads the listener columns, ``start_neurocomment`` reads the
registry. This file holds both halves, the legitimate cases they must not touch, the
card that has to say so before either refusal fires, and the race that only the shared
account lock rules out.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from core.repositories.neurocomment import set_listener_account_id, set_listener_running
from services import _account_owner
from services.neurocomment import _runtime as nc_runtime
from services.neurocomment import _runtime_operations as nc_operations
from services.neuroshilling import _runtime, campaigns, engine
from services.neuroshilling.campaigns import NeuroshillingConflictError
from tests.services.neuroshilling.helpers import seed_campaign

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Long enough for the other coroutine to reach its gate — every wait here is on an
# event a peer sets a few in-process reads later — and short enough that the one wait
# the fix makes unreachable does not slow the suite down.
_GATE_SECONDS = 0.3


@pytest_asyncio.fixture(autouse=True)
async def _quiet_listener(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Start the listener without any of its Telegram calls, and give the globals back.

    Three seams would reach Telegram or outlive the test: stopping the listener a Start
    is moving OFF an account, subscribing the new one, and the onboarding task. None is
    the behaviour under test — the refusal is raised before the first of them — and all
    three are past the point every assertion here reads. The fixture's own teardown is
    what stops one test's listener from being the next test's: the neuroshilling
    conftest resets the neuroshilling globals, not neurocomment's.
    """

    async def _no_reconcile(_account_id: str) -> None:
        return None

    async def _no_stop(_account_id: str) -> None:
        return None

    monkeypatch.setattr(nc_runtime, "reconcile_neurocomment_runtime", _no_reconcile)
    monkeypatch.setattr(nc_runtime, "stop_post_listener", _no_stop)
    monkeypatch.setattr(nc_runtime, "_ensure_onboarding_running", lambda *_a, **_k: None)
    yield
    await nc_runtime.reset_for_tests_async()


@pytest.mark.asyncio
async def test_a_campaign_refuses_to_start_on_the_running_listener() -> None:
    seeded = await seed_campaign()
    await nc_runtime.start_neurocomment("acc-2")

    with pytest.raises(NeuroshillingConflictError) as refusal:
        await _runtime.start_campaign(seeded.campaign_id)

    assert refusal.value.code == "account_is_listener"
    # Refused before the first claim, so the whole roster is left alone — not just the
    # one account that is the listener.
    assert _account_owner.owner_of("acc-1") is None
    assert _account_owner.owner_of("acc-2") is None


@pytest.mark.asyncio
async def test_the_listener_refuses_an_account_a_running_campaign_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run is held open: a finished one has already given its roster back."""
    running = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    seeded = await seed_campaign()
    await _runtime.start_campaign(seeded.campaign_id)

    with pytest.raises(nc_operations.ListenerBusyNeuroshillingError):
        await nc_runtime.start_neurocomment("acc-1")

    # Refused before the commit: the listener columns still say "no listener".
    assert await nc_runtime.get_listener_account_id() is None
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_a_campaign_starts_when_no_listener_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remembered but PAUSED listener does not hold its account, same as for warming."""
    running = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    seeded = await seed_campaign()
    await set_listener_account_id("acc-2")
    await set_listener_running(running=False)

    status = await _runtime.start_campaign(seeded.campaign_id)

    assert status is not None
    assert status.status == "running"
    assert _account_owner.owner_of("acc-2") == "neuroshilling"
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_a_campaign_starts_on_the_account_the_listener_moved_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal names the CURRENT listener, not every account that ever was one."""
    running = asyncio.Event()

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    seeded = await seed_campaign()
    await nc_runtime.start_neurocomment("acc-2")
    await nc_runtime.start_neurocomment("acc-9")

    status = await _runtime.start_campaign(seeded.campaign_id)

    assert status is not None
    assert status.status == "running"
    assert _account_owner.owner_of("acc-2") == "neuroshilling"
    await _runtime.stop_campaign(seeded.campaign_id)


@pytest.mark.asyncio
async def test_the_board_marks_the_account_the_listener_is_running_on() -> None:
    """The refusal must be readable BEFORE Start, or it is a late silent one.

    ``busy_owner`` greys the row out in the picker and the launch card turns it into a
    blocking reason, so this is where the operator learns the roster cannot run — rather
    than from a 409 after pressing the button.
    """
    seeded = await seed_campaign()
    await nc_runtime.start_neurocomment("acc-2")

    board = await campaigns.load_board(seeded.campaign_id)

    assert board is not None
    # ``neurocomment``, the owner the picker already knows, because that is the feature
    # holding the session; no campaign name, because a listener is not one.
    held = {item.account_id: (item.busy_owner, item.busy_campaign_name) for item in board.available}
    assert held == {"acc-1": (None, None), "acc-2": ("neurocomment", None)}


@pytest.mark.asyncio
async def test_the_board_leaves_the_account_of_a_paused_listener_free() -> None:
    """Same reading the refusal takes, so the card cannot say busy where Start says go."""
    seeded = await seed_campaign()
    await set_listener_account_id("acc-2")
    await set_listener_running(running=False)

    board = await campaigns.load_board(seeded.campaign_id)

    assert board is not None
    assert [item.busy_owner for item in board.available] == [None, None]


@pytest.mark.asyncio
async def test_starting_a_campaign_and_the_listener_at_once_lets_exactly_one_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one interleaving in which both sides would otherwise pass, forced by hand.

    Each side publishes AFTER the other reads: the campaign reads the listener columns
    and then claims the registry, the listener reads the registry and then writes those
    columns. So there is exactly one order that gets both through — the listener checks
    the registry, the campaign runs its whole claim, the listener commits — and a plain
    ``gather`` does not produce it, because the campaign has more suspension points
    before its read than the listener has before its write. This drives that order.

    Under the fix the campaign is holding the roster's lifecycle locks by the time it
    is released, so the listener's second gate can only expire: it commits, gives the
    lock back, and the campaign then reads a listener that is running on its account.
    An expiring wait is the shape of the proof, not a hedge — the whole point of the
    lock is that the other side cannot get there while it is held.
    """
    running = asyncio.Event()
    listener_checked = asyncio.Event()
    campaign_claimed = asyncio.Event()
    real_claim = _runtime._claim_accounts
    real_previous = nc_runtime._runtime_get_listener_account_id

    async def _never_ends(_campaign_id: str, _run_id: str) -> None:
        await running.wait()

    async def _claim_after_the_listener_looked(campaign_id: str, account_ids: list[str]) -> None:
        await asyncio.wait_for(listener_checked.wait(), timeout=_GATE_SECONDS)
        try:
            await real_claim(campaign_id, account_ids)
        finally:
            campaign_claimed.set()

    async def _commit_after_the_campaign_claimed() -> str | None:
        # Called by ``start_neurocomment`` right after its registry check and before
        # its listener writes, which is the gap the campaign has to slip into.
        listener_checked.set()
        with suppress(TimeoutError):
            await asyncio.wait_for(campaign_claimed.wait(), timeout=_GATE_SECONDS)
        return await real_previous()

    monkeypatch.setattr(engine, "run_campaign", _never_ends)
    monkeypatch.setattr(_runtime, "_claim_accounts", _claim_after_the_listener_looked)
    monkeypatch.setattr(
        nc_runtime,
        "_runtime_get_listener_account_id",
        _commit_after_the_campaign_claimed,
    )
    seeded = await seed_campaign()

    results = await asyncio.gather(
        _runtime.start_campaign(seeded.campaign_id),
        nc_runtime.start_neurocomment("acc-1"),
        return_exceptions=True,
    )

    failed = [item for item in results if isinstance(item, BaseException)]
    assert len(failed) == 1, results
    assert isinstance(
        failed[0],
        (NeuroshillingConflictError, nc_operations.ListenerBusyNeuroshillingError),
    )
    campaign_holds = _account_owner.owner_of("acc-1") == "neuroshilling"
    listener_holds = await nc_runtime.get_listener_account_id() == "acc-1"
    assert campaign_holds != listener_holds
    await _runtime.stop_campaign(seeded.campaign_id)
