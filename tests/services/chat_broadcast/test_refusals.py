"""A chat is never given to an account it banned or muted: every way a chat changes hands."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from services.chat_broadcast import _moves, _rotation, _schedule
from tests.services.chat_broadcast.fakes import refused
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusKind
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_LONG_REST = 6 * 3600


def _banned(*accounts: str) -> str:
    """``refused_by`` as the table stores it."""
    return json.dumps(list(accounts))


async def _owner(campaign_id: str, key: str) -> tuple[str | None, str]:
    target = await repository.fetch_target(campaign_id, key)
    assert target is not None
    return target.assigned_account_id, target.state


@pytest.mark.asyncio
async def test_a_hand_over_skips_the_account_that_banned_the_chat() -> None:
    chats = [chat("x", "a1", "a1", refused_by=_banned("a2")), chat("y", "a1", "a1")]
    campaign_id, ctx = await setup(("a1", "a2", "a3"), chats)

    moved = await _moves.hand_over(ctx, "a1", ["a2", "a3"])

    assert moved == 2
    # a2 is the idlest, and would have had both: the chat it was banned in goes to a3.
    assert (await _owner(campaign_id, "x"))[0] == "a3"
    assert (await _owner(campaign_id, "y"))[0] == "a2"


@pytest.mark.asyncio
async def test_a_hand_over_with_nobody_left_to_take_the_chat_leaves_it_waiting() -> None:
    chats = [chat("x", "a1", "a1", refused_by=_banned("a2")), chat("y", "a1", "a1")]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    assert await _moves.hand_over(ctx, "a1", ["a2"]) == 1

    # Waiting for an account (not given to the one it banned); the other chat moved on.
    assert await _owner(campaign_id, "x") == ("a1", "waiting_account")
    assert await _owner(campaign_id, "y") == ("a2", "queued")


@pytest.mark.asyncio
async def test_orphans_are_adopted_by_an_account_that_did_not_ban_the_chat() -> None:
    chats = [
        chat("x", "gone", "gone", refused_by=_banned("a1")),
        chat("nowhere", "gone", "gone", refused_by=_banned("a1", "a2")),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    await _moves.adopt_orphans(ctx, ["a1", "a2"])

    assert (await _owner(campaign_id, "x"))[0] == "a2"
    # Banned in both live accounts: it waits for one that is not, it is not forced on them.
    assert await _owner(campaign_id, "nowhere") == ("gone", "waiting_account")


@pytest.mark.asyncio
async def test_a_chat_waiting_for_an_account_is_not_returned_to_one_that_banned_it() -> None:
    chats = [
        # a1 is inside it, and banned there: not the account to return it to.
        chat("x", "a3", "a1", "waiting_account", refused_by=_banned("a1")),
        chat("y", "a3", None, "waiting_account", refused_by=_banned("a1", "a2")),
    ]
    campaign_id, ctx = await setup(("a1", "a2", "a3"), chats)

    await _moves.reclaim_waiting(ctx, ["a1", "a2"])

    assert (await _owner(campaign_id, "x"))[0] == "a2"
    assert await _owner(campaign_id, "y") == ("a3", "waiting_account")


@pytest.mark.asyncio
async def test_a_banned_chat_that_went_to_an_account_which_rests_long_is_not_handed_back(
    clock: Clock,
) -> None:
    """The case behind it: a1 was banned in x, the chat moved to a2, and a2 rests for hours."""
    campaign_id, ctx = await setup(("a1", "a2"), [chat("x", "a1", "a1"), chat("y", "a2", "a2")])
    banned = await repository.fetch_target(campaign_id, "x")
    assert banned is not None
    await _rotation.reroute(ctx, "a1", banned, "banned")
    assert (await _owner(campaign_id, "x"))[0] == "a2"
    await repository.rest_account(campaign_id, "a2", clock.now() + _LONG_REST, "peer_flood")

    await _rotation.place_resting(ctx)
    await _schedule.tick(ctx, force=True)

    # a1 has room and nothing to do, and is still not the account for that chat; the chat
    # a2 holds that never banned a1 went there as usual.
    assert await _owner(campaign_id, "x") == ("a2", "waiting_account")
    assert (await _owner(campaign_id, "y"))[0] == "a1"


@pytest.mark.asyncio
async def test_a_resting_account_places_the_banned_chat_with_a_third_account_instead(
    clock: Clock,
) -> None:
    chats = [chat("x", "a2", "a2", refused_by=_banned("a1")), chat("busy", "a3", "a3")]
    campaign_id, ctx = await setup(("a1", "a2", "a3"), chats)
    await repository.rest_account(campaign_id, "a2", clock.now() + _LONG_REST, "peer_flood")

    assert await _rotation.place_resting(ctx) == 1

    # a1 is idler than a3, and the chat is not for it.
    assert (await _owner(campaign_id, "x"))[0] == "a3"


@pytest.mark.asyncio
async def test_a_reroute_picks_among_the_accounts_the_chat_has_not_refused() -> None:
    campaign_id, ctx = await setup(
        ("a1", "a2", "a3"),
        [chat("x", "a1", "a1", refused_by=_banned("a2")), chat("busy", "a3", "a3")],
    )
    target = await repository.fetch_target(campaign_id, "x")
    assert target is not None

    await _rotation.reroute(ctx, "a1", target, "banned")

    # a2 has no chats and a3 has one: the chat still goes to a3.
    moved = await repository.fetch_target(campaign_id, "x")
    assert moved is not None
    assert (moved.assigned_account_id, moved.state) == ("a3", "queued")
    assert moved.refused_by == ["a2", "a1"]


@pytest.mark.asyncio
async def test_a_reroute_with_every_other_account_refused_skips_the_chat() -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("x", "a1", "a1", refused_by=_banned("a2"))])
    target = await repository.fetch_target(campaign_id, "x")
    assert target is not None

    await _rotation.reroute(ctx, "a1", target, "banned")

    skipped = await repository.fetch_target(campaign_id, "x")
    assert skipped is not None
    assert (skipped.state, skipped.skip_reason, skipped.assigned_account_id) == (
        "skipped",
        "banned",
        "a1",
    )


async def _ban_in_a_chat_already_moved(
    telegram: FakeTelegram, spam: SpamStatusKind
) -> tuple[str, str]:
    """a1 is refused in a chat that already moved once; @SpamBot says ``spam`` of it."""
    telegram.spam["a1"] = spam
    campaign_id, ctx = await setup(
        ("a1", "a2"), [chat("x", "a1", "a1", "waiting", handed_from_account_id="a2")]
    )
    target = await repository.fetch_target(campaign_id, "x")
    assert target is not None
    outcome = await _rotation.account_refused(
        ctx,
        "a1",
        target,
        "account_spam",
        refused(error_type="UserBannedInChannelError"),
    )
    return campaign_id, outcome


@pytest.mark.asyncio
async def test_a_clean_account_banned_in_a_chat_that_already_moved_loses_only_that_chat(
    telegram: FakeTelegram,
) -> None:
    campaign_id, outcome = await _ban_in_a_chat_already_moved(telegram, "clean")

    assert outcome == "acted"
    skipped = await repository.fetch_target(campaign_id, "x")
    assert skipped is not None
    assert (skipped.state, skipped.skip_reason) == ("skipped", "banned")
    [a1, _a2] = await repository.list_roster(campaign_id)
    # Not rested for hours as if it carried a spam block.
    assert (a1.state, a1.resting_until_unix) == ("active", None)


@pytest.mark.asyncio
async def test_a_real_spam_block_still_rests_the_account_in_a_chat_that_already_moved(
    clock: Clock, telegram: FakeTelegram
) -> None:
    campaign_id, outcome = await _ban_in_a_chat_already_moved(telegram, "limited")

    assert outcome == "resting"
    [a1, _a2] = await repository.list_roster(campaign_id)
    limits = settings.chat_broadcast
    assert a1.resting_until_unix == clock.now() + int(
        limits.spam_rest_seconds + limits.rest_margin_seconds
    )
    assert a1.rest_reason == "UserBannedInChannelError"
    target = await repository.fetch_target(campaign_id, "x")
    assert target is not None
    assert target.state != "skipped"
