"""Which lease fence costs the account the rest of its day, and which one must not.

``_reservation`` books the whole remaining daily budget before a cycle runs and hands
the unspent part back on every abnormal exit -- that is #208, and
``test_reservation_recovery`` guards it from both directions.

``_run_reserved_cycle`` exempts exactly one exit from that rule: the fence *after*
dispatch, where the request is already outside the process and the Telegram outcome is
unknowable, so spending the budget is the conservative reading. Its sibling before
dispatch describes the opposite situation -- no request was issued, nothing is unknown
-- and while both raised the same type the exemption could not tell them apart, so a
Stop that landed a moment before the first ``execute`` parked the account on a daily
limit it had never spent.

The three tests here are the same physical situation reached three ways: nothing
dispatched and the day survives (pre-dispatch fence, cancellation), something
dispatched and the day is forfeit (post-dispatch fence).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_warming_state
from services import warming
from services.warming import _loop, _seams
from tests.services.warming._support import (
    _no_quiet_days,
    _ok,
    _seed_warming_account,
)

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction


class _GatewayRecorder:
    """Stands in for the real gateway so a dispatch cannot go unnoticed."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(
        self,
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "warming",  # noqa: ARG002 - mirrors the real signature
    ) -> ActionResult:
        self.calls.append(action.action_type)
        return _ok(account_id, action)


@pytest.mark.asyncio
async def test_a_lease_revoked_before_the_first_dispatch_keeps_the_rest_of_the_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop revokes the lease, no Telegram request is issued, and the day survives.

    ``_cancel_runtime_task`` revokes the lease *first* and only then cancels, and the
    whole point of the lease is that the cancellation may not land (a task that
    suppresses it is the case the fence exists for). This test occupies exactly that
    window: the lease is gone, the cancellation has not arrived, and the cycle walks
    into its first ``execute``.

    The gateway recorder proves what makes the hand-back correct rather than reckless:
    **zero** Telegram requests were issued, so there is no ambiguous outcome to be
    conservative about, and every other exit that dispatched nothing gives the budget
    back. The one action counted is the presence flip the cycle books *before* the
    call goes out -- deliberate, because a cancellation landing mid-RPC must count as
    spent -- and one action is not a day.
    """
    _no_quiet_days(monkeypatch)
    gateway = _GatewayRecorder()
    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    await _seed_warming_account(run_id="run-1")

    # The generation owns the lease, then Stop revokes it before the cycle dispatches.
    _seams.activate_lease("acc-1", "run-1")
    _seams.revoke_lease("acc-1", "run-1")

    with (
        _seams.lease_scope("acc-1", "run-1"),
        pytest.raises(_seams.WarmingLeaseRevokedError) as raised,
    ):
        await warming.run_loop_iteration("acc-1", run_id="run-1")

    # Nothing was sent: the pre-dispatch fence fired, not the post-dispatch one.
    assert gateway.calls == []
    assert not isinstance(raised.value, _seams.WarmingLeaseLostMidDispatchError)

    # The booking was handed back down to the one attempt the cycle had booked.
    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == 1

    # ...so the next iteration warms instead of meeting a limit it never spent.
    result = await warming.run_loop_iteration("acc-1", run_id="run-1")

    assert result.detail != "daily limit"


@pytest.mark.asyncio
async def test_a_cancelled_cycle_that_dispatched_nothing_keeps_its_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contrast case, to show the hand-back is not specific to the lease exit.

    Same account, same booking, same "nothing was dispatched" -- but the cycle unwinds
    on ``CancelledError`` instead, so ``_release_reservation_on_exit`` runs and the day
    survives. Both exits describe the same physical situation, and now both cost the
    operator the same nothing.
    """
    _no_quiet_days(monkeypatch)

    async def cancel_before_dispatch(
        _account_id: str,
        _action: TelegramAction,
        *,
        domain: str = "warming",
    ) -> ActionResult:
        raise NotImplementedError  # pragma: no cover - never reached

    monkeypatch.setattr(_seams, "_gateway_execute", cancel_before_dispatch)
    await _seed_warming_account(run_id="run-1")

    async def cancel_the_cycle(*_args: object, **_kwargs: object) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(_loop, "_execute_cycle", cancel_the_cycle)

    with pytest.raises(asyncio.CancelledError):
        await warming.run_loop_iteration("acc-1", run_id="run-1")

    record = await fetch_warming_state("acc-1")
    assert record is not None
    # Handed back: today's count is untouched, not the booked phase cap.
    assert record.daily_actions == 0


@pytest.mark.asyncio
async def test_a_lease_revoked_mid_dispatch_still_forfeits_the_whole_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exemption the other two must not swallow: a request whose fate is unknown.

    The gateway here does what a real cancellation-suppressing RPC does -- it goes out,
    and the lease disappears while it is in flight. What Telegram did with that request
    and everything it might still be doing is unknowable from here, so the whole
    remaining budget stays booked and the account waits for the next UTC midnight.
    That is the conservative choice, and separating the two fences must not weaken it.
    """
    _no_quiet_days(monkeypatch)
    gateway = _GatewayRecorder()

    async def revoke_mid_flight(
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "warming",
    ) -> ActionResult:
        result = await gateway(account_id, action, domain=domain)
        _seams.revoke_lease(account_id, "run-1")
        return result

    monkeypatch.setattr(_seams, "_gateway_execute", revoke_mid_flight)
    await _seed_warming_account(run_id="run-1")
    _seams.activate_lease("acc-1", "run-1")

    with (
        _seams.lease_scope("acc-1", "run-1"),
        pytest.raises(_seams.WarmingLeaseLostMidDispatchError),
    ):
        await warming.run_loop_iteration("acc-1", run_id="run-1")

    # The request really was dispatched — that is the whole difference.
    assert gateway.calls == ["set_online"]

    record = await fetch_warming_state("acc-1")
    assert record is not None
    assert record.daily_actions == settings.warming.phase_daily_cap["intro"]
