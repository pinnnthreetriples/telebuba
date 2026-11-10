"""A worker that read a chat before it changed hands cannot act on it under its new owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _join, _moves, _send
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import FakeTelegram


async def _read_then_handed(
    *, member: str | None, state: str = "queued"
) -> tuple[str, RunContext, TargetRecord]:
    """a1 reads its chat; before it acts the chat is handed to a2 (the balance, a hand-over)."""
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a1", member, state)])
    stale = await repository.fetch_target(campaign_id, "chat0")
    assert stale is not None
    assert await _moves.assign(
        ctx,
        stale,
        "a2",
        expect_states=_moves.REBALANCE_STATES,
        record_origin=False,
    )
    return campaign_id, ctx, stale


@pytest.mark.asyncio
async def test_a_join_started_from_a_stale_read_goes_nowhere(telegram: FakeTelegram) -> None:
    campaign_id, ctx, stale = await _read_then_handed(member=None)

    step = await _join.join(ctx, "a1", stale)

    assert step == "idle"
    assert telegram.joins == []
    target = await repository.fetch_target(campaign_id, "chat0")
    assert target is not None
    assert (target.state, target.assigned_account_id) == ("queued", "a2")


@pytest.mark.asyncio
async def test_entering_as_a_member_from_a_stale_read_goes_nowhere(
    telegram: FakeTelegram,
) -> None:
    telegram.members.add(("a1", "chat0"))
    campaign_id, ctx, stale = await _read_then_handed(member=None)

    step = await _join.join(ctx, "a1", stale)

    assert step == "idle"
    target = await repository.fetch_target(campaign_id, "chat0")
    assert target is not None
    assert (target.state, target.member_account_id) == ("queued", None)


@pytest.mark.asyncio
async def test_an_approval_poll_from_a_stale_read_does_not_enter_the_chat(
    telegram: FakeTelegram,
) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a2", None, "pending_approval")])
    current = await repository.fetch_target(campaign_id, "chat0")
    assert current is not None
    # What a1 read before the chat went to a2.
    stale = current.model_copy(update={"assigned_account_id": "a1"})

    step = await _join.poll_approval(ctx, "a1", stale)

    assert step == "idle"
    assert telegram.joins == []
    target = await repository.fetch_target(campaign_id, "chat0")
    assert target is not None
    assert (target.state, target.assigned_account_id) == ("pending_approval", "a2")


@pytest.mark.asyncio
async def test_a_chain_started_from_a_stale_read_sends_nothing(telegram: FakeTelegram) -> None:
    campaign_id, ctx, stale = await _read_then_handed(member="a1")

    step = await _send.play_chain(ctx, "a1", stale)

    assert step == "idle"
    assert telegram.sent == []
    target = await repository.fetch_target(campaign_id, "chat0")
    assert target is not None
    assert (target.state, target.assigned_account_id, target.step_index) == ("queued", "a2", 0)
    assert await repository.list_journal(campaign_id, "run-1") == []


@pytest.mark.asyncio
async def test_the_new_owner_plays_the_chat_the_old_one_could_not(telegram: FakeTelegram) -> None:
    campaign_id, ctx, _stale = await _read_then_handed(member="a2")
    fresh = await repository.fetch_target(campaign_id, "chat0")
    assert fresh is not None

    step = await _send.play_chain(ctx, "a2", fresh)

    assert step == "acted"
    assert [s.account_id for s in telegram.sent] == ["a2"]
