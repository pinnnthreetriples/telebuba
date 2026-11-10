"""Two scheduler passes at once, and the groups an account reaches by id (own mode)."""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _moves, _schedule, _worker
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import FakeTelegram


@pytest.mark.asyncio
async def test_two_ticks_at_once_never_move_a_chat_twice() -> None:
    chats = [chat(f"chat{index}", "a1", "a1") for index in range(6)]
    campaign_id, ctx = await setup(("a1", "a2", "a3", "a4"), chats)

    await asyncio.gather(
        _schedule.tick(ctx, force=True),
        _schedule.tick(ctx, force=True),
        _schedule.tick(ctx, force=True),
    )

    handed = [e for e in await repository.list_events(campaign_id) if e.kind == "handed"]
    # The passes take turns: the second finds the work done instead of doing it again.
    assert len(handed) == 4
    assert max(Counter(e.chat_key for e in handed).values()) == 1
    load = _moves.open_load(await repository.list_targets(campaign_id))
    assert sorted(load[a] for a in ("a1", "a2", "a3", "a4")) == [1, 1, 2, 2]


@pytest.mark.asyncio
async def test_a_group_reached_by_id_with_a_username_is_balanced_and_joined_by_it(
    telegram: FakeTelegram,
) -> None:
    """Own mode: a public group has a username, so another account can join it by that."""
    chats = [chat(f"own{index}", "a1", "a1", kind="own", peer_id=500 + index) for index in range(4)]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    assert await _schedule.rebalance(ctx, ["a1", "a2"]) == 2

    moved = [t for t in await repository.list_targets(campaign_id) if t.assigned_account_id == "a2"]
    # Handed over like any chat: the receiver is not inside, so the chat starts over at a join.
    assert [(t.state, t.member_account_id) for t in moved] == [("queued", "a1")] * 2
    first = moved[0]

    step = await _worker.handle(ctx, "a2", first)

    assert step == "acted"
    assert telegram.joins == [("a2", first.chat_key)]
    joined = await repository.fetch_target(campaign_id, first.chat_key)
    assert joined is not None
    assert (joined.state, joined.member_account_id, joined.peer_id) == ("waiting", "a2", 500)
