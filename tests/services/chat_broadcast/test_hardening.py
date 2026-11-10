"""Faults and dead ends that must not stop a run: a stuck donor, a bad tick, a lost step."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from core.repositories.logs import list_recent_logs
from services.chat_broadcast import _coordinator, _join, _moves, _rewrite, _schedule, _worker
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastMessage
    from schemas.chat_broadcast_records import TargetRecord
    from schemas.logs import LogEntry
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_BOOM = "boom: a proxy or a path would be here"


async def _owners(campaign_id: str) -> dict[str, str | None]:
    return {t.chat_key: t.assigned_account_id for t in await repository.list_targets(campaign_id)}


async def _crash_logs() -> list[LogEntry]:
    rows = await list_recent_logs(limit=300)
    return [row for row in rows if row.event == "chat_broadcast_worker_crashed"]


async def _balance(campaign_id: str, ctx: RunContext) -> int:
    from services.chat_broadcast import _rotation  # noqa: PLC0415

    return await _schedule.rebalance(ctx, await _rotation.live_accounts(campaign_id))


@pytest.mark.asyncio
async def test_a_donor_with_nothing_movable_gives_way_to_the_next_donor() -> None:
    chats = [chat(f"stuck{index}", "a1", "a1", "pending_approval") for index in range(3)]
    chats += [chat(f"free{index}", "a2", "a2") for index in range(3)]
    campaign_id, ctx = await setup(("a1", "a2", "a3"), chats)

    moved = await _balance(campaign_id, ctx)

    owners = await _owners(campaign_id)
    # a1 is the busiest and cannot give; a2's chats still reach the idle a3.
    assert moved == 1
    assert [owners[f"stuck{index}"] for index in range(3)] == ["a1", "a1", "a1"]
    assert sorted(owners[f"free{index}"] for index in range(3)) == ["a2", "a2", "a3"]


@pytest.mark.asyncio
async def test_a_donor_whose_chats_the_receiver_cannot_enter_gives_way_too() -> None:
    chats = [
        chat(f"own{index}", "a1", "a1", kind="own", username=None, peer_id=900 + index)
        for index in range(3)
    ]
    chats += [chat(f"open{index}", "a2", "a2") for index in range(3)]
    campaign_id, ctx = await setup(("a1", "a2", "a3"), chats)

    await _balance(campaign_id, ctx)

    owners = await _owners(campaign_id)
    assert {owners[f"own{index}"] for index in range(3)} == {"a1"}
    assert "a3" in {owners[f"open{index}"] for index in range(3)}


@pytest.mark.asyncio
async def test_a_receiver_that_spent_its_join_budget_is_only_given_chats_it_is_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chats = [
        chat("inside", "a1", "a2"),
        chat("fresh0", "a1", None),
        chat("fresh1", "a1", "a1"),
        chat("fresh2", "a1", "a1"),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    async def capped(account_id: str) -> bool:
        return account_id == "a2"

    monkeypatch.setattr(_join, "at_join_cap", capped)

    await _balance(campaign_id, ctx)

    owners = await _owners(campaign_id)
    # a2 may take the chat it is already in, and nothing that needs a join.
    assert owners["inside"] == "a2"
    assert {owners[key] for key in ("fresh0", "fresh1", "fresh2")} == {"a1"}


@pytest.mark.asyncio
async def test_a_transient_fault_in_the_supervisor_is_logged_and_the_run_goes_on(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    real = _coordinator._Tasks.ensure
    calls: list[int] = []

    async def ensure(self: _coordinator._Tasks) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(_BOOM)
        await real(self)

    monkeypatch.setattr(_coordinator._Tasks, "ensure", ensure)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("done", None)
    assert telegram.texts() == ["Hi"]
    [crash] = await _crash_logs()
    assert crash.account_id is None
    assert crash.extra == {"campaign_id": campaign_id, "error_type": "RuntimeError"}
    assert _BOOM not in repr(crash)


@pytest.mark.asyncio
async def test_a_fault_while_the_supervisor_settles_a_task_does_not_fail_the_run(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("one", "two"))
    telegram.send_answers["a1"] = [refused("flood_wait", flood_wait_seconds=3000)]
    real = _moves.reclaim_waiting
    calls: list[int] = []

    async def reclaim(ctx: RunContext, live: list[str]) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(_BOOM)
        await real(ctx, live)

    monkeypatch.setattr(_moves, "reclaim_waiting", reclaim)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert telegram.texts() == ["one", "two"]
    assert len(await _crash_logs()) == 1


@pytest.mark.asyncio
async def test_a_failing_scheduler_is_not_counted_against_the_accounts(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@alpha", "@beta", "@gamma"),
        messages=("Hi",),
        max_consecutive_errors=1,
    )

    async def broken(_ctx: RunContext, **_options: bool) -> None:
        raise RuntimeError(_BOOM)

    monkeypatch.setattr(_schedule, "tick", broken)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert len(telegram.sent) == 3
    # One error in a row would have halted either account had the tick been charged to it.
    roster = await repository.list_roster(campaign_id)
    assert {(r.state, r.consecutive_errors) for r in roster} == {("active", 0)}
    crashes = await _crash_logs()
    assert crashes
    assert {row.account_id for row in crashes} <= {"a1", "a2", None}


@pytest.mark.asyncio
async def test_a_step_whose_text_could_not_be_made_is_sent_later_not_lost(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    real = _rewrite.compose
    calls: list[int] = []

    async def compose(
        ctx: RunContext, step_index: int, message: ChatBroadcastMessage, target: TargetRecord
    ) -> tuple[str, bool]:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(_BOOM)
        return await real(ctx, step_index, message, target)

    monkeypatch.setattr(_rewrite, "compose", compose)

    await run_to_end(campaign_id)

    # The reservation made before the failure was given back: the step went out once.
    assert telegram.texts() == ["Hi"]
    statuses = [row.status for row in await repository.list_journal(campaign_id)]
    assert statuses == ["sent"]


@pytest.mark.asyncio
async def test_a_chat_through_for_good_does_not_shorten_the_workers_sleep(clock: Clock) -> None:
    long_ago = clock.now() - 3600
    chats = [
        chat(
            "failed", "a1", "a1", "skipped", skip_reason="error", round=1, next_action_unix=long_ago
        ),
        chat("resting", "a1", "a1", "round_done", round=1, next_action_unix=clock.now() + 600),
    ]
    campaign_id, ctx = await setup(("a1",), chats, loop=False)
    targets = await repository.list_targets(campaign_id)

    # One round only: the error skip is never retried, so its old time is nothing to wake for
    # (and the resting chat, a legacy leftover of the last round, is promoted, not waited for).
    assert not _schedule.will_return(targets[0], ctx.rounds)
    assert (
        _worker._idle(ctx, targets[:1], "a1", clock.now())
        == settings.chat_broadcast.idle_poll_seconds
    )

    more = replace(ctx, settings=ctx.settings.model_copy(update={"loop": True, "rounds": 3}))
    assert _schedule.will_return(targets[0], more.rounds)
    assert _worker._idle(more, targets[:1], "a1", clock.now()) == 1.0
