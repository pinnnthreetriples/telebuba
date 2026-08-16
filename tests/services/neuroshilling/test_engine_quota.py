"""The three posting ceilings, and the lock that makes them mean anything.

The lock is the point of this file. Roles belong to the campaign, so one account plays
one role in every target, and two campaigns may share an account outright: without
serialising ``[re-count -> insert pending row]`` both read an under-cap total and both
publish. The count predicate has to include ``pending`` for the same reason — a row
that only appears after a successful send is invisible for the whole length of it.

Serialised is only half of it: the lock must also be LET GO before the send, or it
nests outside ``services.warming.account_lock`` and the lifecycle mutex behind it. One
test pins each half, because a change that breaks the second passes the first.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from core.db import _get_engine
from core.repositories.neuroshilling._tables import _neuroshilling_messages
from schemas.neuroshilling_scenario import NeuroshillingStepInput
from schemas.telegram_actions import ResolveChatResult
from services.neuroshilling import _seams, _steps, _telegram, engine
from tests.services.neuroshilling.helpers import seed_campaign, sent

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, TelegramAction

_RUN = "run-1"


async def _statuses() -> list[str]:
    def _read() -> list[str]:
        statement = select(_neuroshilling_messages.c.status).order_by(
            _neuroshilling_messages.c.id,
        )
        with _get_engine().connect() as connection:
            return [str(status) for (status,) in connection.execute(statement)]

    return await asyncio.to_thread(_read)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> list[TelegramAction]:
    seen: list[TelegramAction] = []

    async def _execute(_account_id: str, action: TelegramAction) -> ActionResult:
        seen.append(action)
        return sent(100 + len(seen))

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "joined"

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    return seen


def _solo_steps(count: int) -> list[NeuroshillingStepInput]:
    """``count`` lines, all spoken by the first role, so one account carries them all."""
    return [
        NeuroshillingStepInput(
            role_id="#0",
            text=f"line {index}",
            delay_min_seconds=0,
            delay_max_seconds=0,
        )
        for index in range(count)
    ]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_hourly_cap_skips_the_step(gateway: list[TelegramAction]) -> None:
    seeded = await seed_campaign(
        accounts=("acc-1",),
        steps=_solo_steps(3),
        messages_per_hour=2,
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(gateway) == 2
    assert await _statuses() == ["sent", "sent", "skipped"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_per_chat_daily_cap_skips_the_step(gateway: list[TelegramAction]) -> None:
    seeded = await seed_campaign(
        accounts=("acc-1",),
        steps=_solo_steps(3),
        messages_per_chat_per_day=1,
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(gateway) == 1
    assert await _statuses() == ["sent", "skipped", "skipped"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_a_zero_per_chat_cap_means_no_ceiling(gateway: list[TelegramAction]) -> None:
    """Zero is the operator's "unlimited", not a cap of nothing."""
    seeded = await seed_campaign(
        accounts=("acc-1",),
        steps=_solo_steps(3),
        messages_per_chat_per_day=0,
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(gateway) == 3


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_lifetime_cap_skips_the_step(gateway: list[TelegramAction]) -> None:
    seeded = await seed_campaign(
        accounts=("acc-1",),
        steps=_solo_steps(3),
        total_per_account=2,
    )

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert len(gateway) == 2


@pytest.mark.usefixtures("no_sleep", "gateway")
@pytest.mark.asyncio
async def test_the_recount_and_the_insert_share_one_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two campaigns on one account, sending at the same moment, must not both pass.

    The re-count is stalled inside the lock while a sibling runs, which is the race a
    sequential test cannot reach: without the lock both read a count of zero, both
    insert, and the account publishes twice its hourly cap of one.
    """
    first = await seed_campaign(accounts=("acc-1",), steps=_solo_steps(1), messages_per_hour=1)
    second = await seed_campaign(
        accounts=("acc-1",),
        targets="@beta",
        steps=_solo_steps(1),
        messages_per_hour=1,
    )
    entered = asyncio.Event()
    resume = asyncio.Event()
    real_reason = _steps._quota_reason
    stalled = False

    async def _stalling(campaign: object, account_id: str, target: str) -> str | None:
        nonlocal stalled
        if not stalled:
            stalled = True
            entered.set()
            await resume.wait()
        return await real_reason(campaign, account_id, target)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(_steps, "_quota_reason", _stalling)
    one = asyncio.create_task(engine.run_campaign(first.campaign_id, "run-a"))
    await entered.wait()
    two = asyncio.create_task(engine.run_campaign(second.campaign_id, "run-b"))
    # The sibling cannot get past the lock while the first holds it; releasing the
    # first is what lets it re-count, and by then there is a row to count.
    await asyncio.sleep(0)
    resume.set()
    await asyncio.gather(one, two)

    assert sorted(await _statuses()) == ["sent", "skipped"]


@pytest.mark.usefixtures("no_sleep")
@pytest.mark.asyncio
async def test_the_quota_lock_is_let_go_before_the_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held across the dispatch, the lock is a deadlock rather than a ceiling.

    The sibling test above proves the ``[re-count -> insert]`` section is serialised,
    and that stays true if the send is pulled INSIDE ``_reserve``'s ``async with`` —
    the one regression ``services.neuroshilling._steps``' docstring warns about, since
    the quota lock would then be held over ``services.warming.account_lock`` and the
    lifecycle mutex it waits on. Read from inside the gateway call, which is the only
    place the difference between the two arrangements shows.
    """
    held: list[bool] = []

    async def _execute(account_id: str, _action: TelegramAction) -> ActionResult:
        held.append(_steps._account_lock(account_id).locked())
        return sent(100 + len(held))

    async def _resolve(_account_id: str, _action: TelegramAction) -> ResolveChatResult:
        return ResolveChatResult(chat_id=555, kind="megagroup")

    async def _joins(_campaign_id: str, _account_id: str, _target: str) -> str:
        return "joined"

    monkeypatch.setattr(_seams, "execute", _execute)
    monkeypatch.setattr(_seams, "execute_read", _resolve)
    monkeypatch.setattr(_telegram, "join_target", _joins)
    seeded = await seed_campaign(accounts=("acc-1",), steps=_solo_steps(2))

    await engine.run_campaign(seeded.campaign_id, _RUN)

    assert held == [False, False]
