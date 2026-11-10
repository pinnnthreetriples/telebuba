"""The balance and the hand-over of resting accounts' chats, by the scheduler's tick."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast_records import JournalKey
from services import _account_owner
from services.chat_broadcast import _moves, _rotation, _schedule, _state
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import Clock
    from tests.services.chat_broadcast.layout import Chat


async def _owners(campaign_id: str) -> dict[str, str | None]:
    return {t.chat_key: t.assigned_account_id for t in await repository.list_targets(campaign_id)}


async def _load(campaign_id: str, *accounts: str) -> list[int]:
    load = _moves.open_load(await repository.list_targets(campaign_id))
    return sorted(load[account] for account in accounts)


async def _balance(campaign_id: str, ctx: RunContext) -> int:
    return await _schedule.rebalance(ctx, await _rotation.live_accounts(campaign_id))


def _six_on_a1() -> list[Chat]:
    return [chat(f"chat{index}", "a1", "a1") for index in range(6)]


@pytest.mark.asyncio
async def test_the_load_is_shared_until_no_account_has_two_more_than_another() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3", "a4"), _six_on_a1())

    moved = await _balance(campaign_id, ctx)

    assert moved == 4
    assert await _load(campaign_id, "a1", "a2", "a3", "a4") == [1, 1, 2, 2]
    # Deterministic: the roster order decides every tie, the run's own order the chat.
    assert await _owners(campaign_id) == {
        "chat0": "a2",
        "chat1": "a3",
        "chat2": "a4",
        "chat3": "a2",
        "chat4": "a1",
        "chat5": "a1",
    }


@pytest.mark.asyncio
async def test_a_second_pass_finds_nothing_to_move_and_no_chat_goes_back() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3"), _six_on_a1())
    await _balance(campaign_id, ctx)
    settled = await _owners(campaign_id)

    assert await _balance(campaign_id, ctx) == 0
    assert await _balance(campaign_id, ctx) == 0

    assert await _owners(campaign_id) == settled
    assert await _load(campaign_id, "a1", "a2", "a3") == [2, 2, 2]


@pytest.mark.asyncio
async def test_a_gap_of_one_is_left_alone() -> None:
    chats = [chat("chat0", "a1", "a1"), chat("chat1", "a1", "a1"), chat("chat2", "a2", "a2")]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    assert await _balance(campaign_id, ctx) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["writing", "joining", "captcha", "pending_approval"])
async def test_a_chat_with_a_call_in_flight_or_a_request_pending_is_never_moved(
    state: str,
) -> None:
    chats = [chat(f"busy{index}", "a1", "a1", state) for index in range(4)]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    assert await _balance(campaign_id, ctx) == 0

    assert set((await _owners(campaign_id)).values()) == {"a1"}


@pytest.mark.asyncio
async def test_only_the_idle_chats_move_when_the_others_are_in_flight() -> None:
    chats = [
        chat("busy0", "a1", "a1", "writing"),
        chat("busy1", "a1", "a1", "joining"),
        chat("busy2", "a1", "a1", "pending_approval"),
        chat("idle0", "a1", "a1"),
        chat("idle1", "a1", "a1", "waiting"),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    await _balance(campaign_id, ctx)

    owners = await _owners(campaign_id)
    assert [owners[key] for key in ("busy0", "busy1", "busy2")] == ["a1", "a1", "a1"]
    # Both idle ones go: the gap was five to none, and the three in flight stay put.
    assert (owners["idle0"], owners["idle1"]) == ("a2", "a2")


@pytest.mark.asyncio
async def test_a_resting_a_halted_and_a_busy_account_are_not_given_chats() -> None:
    chats = [chat(f"chat{index}", "a1", "a1") for index in range(8)]
    campaign_id, ctx = await setup(("a1", "a2", "a3", "a4", "a5"), chats)
    await repository.rest_account(campaign_id, "a2", 4_000_000_000, "hour_limit")
    await repository.halt_account(campaign_id, "a3", "errors_in_a_row")
    # Taken by another feature: no longer held by this run.
    _account_owner.release("a4", "chat_broadcast", campaign_id)
    assert _account_owner.try_claim("a4", "warming", "w-1") is None

    live = await _rotation.live_accounts(campaign_id)
    await _schedule.rebalance(ctx, live)

    assert live == ["a1", "a5"]
    assert set((await _owners(campaign_id)).values()) == {"a1", "a5"}
    assert await _load(campaign_id, "a1", "a5") == [4, 4]


@pytest.mark.asyncio
async def test_an_account_at_its_limit_is_not_given_chats(clock: Clock) -> None:
    campaign_id, ctx = await setup(
        ("a1", "a2", "a3"), _six_on_a1(), per_hour=1, per_day=50, account_limit=True
    )
    key = JournalKey(run_id="old", chat_key="x", round=1, step_index=0)
    assert await repository.claim_message(
        key, campaign_id=campaign_id, account_id="a2", kind="text"
    )
    await repository.settle_message(key, "sent", text="Hi")
    assert clock.now() > 0

    await _balance(campaign_id, ctx)

    assert set((await _owners(campaign_id)).values()) == {"a1", "a3"}
    assert await _load(campaign_id, "a1", "a3") == [3, 3]


@pytest.mark.asyncio
async def test_a_balancing_move_leaves_the_chats_origin_alone_but_is_in_its_history() -> None:
    campaign_id, ctx = await setup(("a1", "a2"), _six_on_a1())

    await _balance(campaign_id, ctx)

    targets = await repository.list_targets(campaign_id)
    moved = [t for t in targets if t.assigned_account_id == "a2"]
    assert len(moved) == 3
    # "Moved once already" is what a ban or a mute asks: a chat only balanced is not.
    assert all(t.handed_from_account_id is None for t in targets)
    events = [e for e in await repository.list_events(campaign_id) if e.kind == "handed"]
    assert {(e.account_id, e.detail) for e in events} == {("a2", "a1")}
    assert len(events) == 3


@pytest.mark.asyncio
async def test_a_banned_chat_that_was_balanced_can_still_be_moved_once() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3"), _six_on_a1())
    await _balance(campaign_id, ctx)
    balanced = next(
        t for t in await repository.list_targets(campaign_id) if t.assigned_account_id == "a2"
    )

    await _rotation.reroute(ctx, "a2", balanced, "banned")

    moved = await repository.fetch_target(campaign_id, balanced.chat_key)
    assert moved is not None
    assert moved.assigned_account_id != "a2"
    assert moved.state != "skipped"
    assert moved.handed_from_account_id == "a2"


@pytest.mark.asyncio
async def test_the_cheapest_chat_goes_first() -> None:
    # What it costs a2 to take each: inside already, nobody has joined, somebody else is in.
    chats = [
        chat("other0", "a1", "a3"),
        chat("nobody", "a1", None),
        chat("inside", "a1", "a2"),
        chat("other1", "a1", "a3"),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    await _balance(campaign_id, ctx)

    owners = await _owners(campaign_id)
    assert (owners["inside"], owners["nobody"]) == ("a2", "a2")
    assert (owners["other0"], owners["other1"]) == ("a1", "a1")


@pytest.mark.asyncio
async def test_a_resting_chat_changes_owner_and_keeps_its_rest(clock: Clock) -> None:
    until = clock.now() + 1800
    chats = [
        chat("rest", "a1", "a1", "round_done", next_action_unix=until, round=3),
        chat("busy", "a1", "a1", "writing"),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    assert await _balance(campaign_id, ctx) == 1

    resting = await repository.fetch_target(campaign_id, "rest")
    assert resting is not None
    assert (resting.assigned_account_id, resting.state, resting.round) == ("a2", "round_done", 3)
    assert resting.next_action_unix == until


@pytest.mark.asyncio
async def test_a_chat_about_to_be_played_goes_before_a_resting_one() -> None:
    chats = [
        chat("rest", "a1", "a1", "round_done", next_action_unix=2_000_000_000),
        chat("queued", "a1", "a1"),
        chat("also", "a1", "a1"),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    await _balance(campaign_id, ctx)

    owners = await _owners(campaign_id)
    assert owners["rest"] == "a1"
    assert sorted((owners["queued"], owners["also"])) == ["a1", "a2"]


@pytest.mark.asyncio
async def test_with_fewer_chats_than_accounts_nothing_goes_back_and_forth() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a3"), [chat("only", "a1", "a1")])

    for _pass in range(3):
        assert await _balance(campaign_id, ctx) == 0

    assert (await _owners(campaign_id))["only"] == "a1"


@pytest.mark.asyncio
async def test_a_single_live_account_has_nobody_to_give_to() -> None:
    campaign_id, ctx = await setup(("a1",), _six_on_a1())

    assert await _balance(campaign_id, ctx) == 0


@pytest.mark.asyncio
async def test_a_chat_that_changed_state_under_the_balance_stays_and_the_walk_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), _six_on_a1())
    attempts = 0

    async def lost(*_args: object, **_kwargs: object) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    monkeypatch.setattr(_moves, "assign", lost)

    assert await _balance(campaign_id, ctx) == 0

    # Every chat tried once, none forced: the walk ends instead of trying one forever.
    assert attempts == 6
    assert set((await _owners(campaign_id)).values()) == {"a1"}


@pytest.mark.asyncio
async def test_the_scheduler_balances_on_a_forced_tick_and_throttles_the_rest(
    clock: Clock,
) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), _six_on_a1())

    await _schedule.tick(ctx)
    assert await _load(campaign_id, "a1", "a2") == [3, 3]
    # Chats given back to the busy account are not balanced again within the throttle ...
    for key in ("chat0", "chat1"):
        await repository.update_target(campaign_id, key, assigned_account_id="a1")
    await _schedule.tick(ctx)
    assert await _load(campaign_id, "a1", "a2") == [1, 5]
    # ... unless forced, and after the throttle has passed.
    await _schedule.tick(ctx, force=True)
    assert await _load(campaign_id, "a1", "a2") == [3, 3]
    for key in ("chat0", "chat1"):
        await repository.update_target(campaign_id, key, assigned_account_id="a1")
    clock.advance(60)
    await _schedule.tick(ctx)
    assert await _load(campaign_id, "a1", "a2") == [3, 3]


@pytest.mark.asyncio
async def test_a_chat_that_came_up_while_its_owner_rests_long_goes_to_a_live_account(
    clock: Clock,
) -> None:
    chats = [
        chat("due", "a1", "a1", "round_done", next_action_unix=clock.now() - 5),
        chat("later", "a1", "a1", "round_done", next_action_unix=clock.now() + 9000),
        chat("theirs", "a2", "a2"),
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)
    await repository.rest_account(campaign_id, "a1", clock.now() + 6 * 3600, "peer_flood")

    await _schedule.tick(ctx)

    due = await repository.fetch_target(campaign_id, "due")
    later = await repository.fetch_target(campaign_id, "later")
    assert due is not None
    assert later is not None
    # Promoted into round 2 and given to the account that can write, not left for 6 hours.
    assert (due.state, due.round, due.assigned_account_id) == ("queued", 2, "a2")
    assert due.handed_from_account_id == "a1"
    # Still resting: it moves when its own time comes.
    assert (later.state, later.assigned_account_id) == ("round_done", "a1")


@pytest.mark.asyncio
async def test_placing_a_resting_accounts_chats_twice_changes_nothing_the_second_time(
    clock: Clock,
) -> None:
    chats = [chat(f"chat{index}", "a1", "a1") for index in range(3)] + [chat("mine", "a2", "a2")]
    campaign_id, ctx = await setup(("a1", "a2"), chats)
    await repository.rest_account(campaign_id, "a1", clock.now() + 6 * 3600, "peer_flood")

    assert await _rotation.place_resting(ctx) == 3
    assert await _rotation.place_resting(ctx) == 0

    assert set((await _owners(campaign_id)).values()) == {"a2"}


@pytest.mark.asyncio
async def test_a_short_rest_keeps_its_chats_and_a_nearly_over_one_places_nothing(
    clock: Clock,
) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a1", "a1")])
    await repository.rest_account(campaign_id, "a1", clock.now() + 30, "flood_wait")

    assert await _rotation.place_resting(ctx) == 0
    clock.advance(60)
    assert await _rotation.place_resting(ctx) == 0

    assert (await _owners(campaign_id))["chat0"] == "a1"


@pytest.mark.asyncio
async def test_concurrent_promotions_of_one_chat_advance_it_by_one_round(clock: Clock) -> None:
    chats = [chat("chat0", "a1", "a1", "round_done", next_action_unix=clock.now() - 1)]
    campaign_id, ctx = await setup(("a1", "a2"), chats)
    targets = await repository.list_targets(campaign_id)

    moved = await asyncio.gather(
        _schedule.promote(ctx, targets), _schedule.promote(ctx, targets, owner="a1")
    )

    assert sum(moved) == 1
    promoted = await repository.fetch_target(campaign_id, "chat0")
    assert promoted is not None
    assert (promoted.state, promoted.round) == ("queued", 2)


@pytest.mark.asyncio
async def test_a_chat_that_rested_through_its_last_round_is_done_and_one_before_it_is_not(
    clock: Clock,
) -> None:
    due = {"next_action_unix": clock.now() - 1}
    chats = [
        chat("last", "a1", "a1", "round_done", round=1, **due),
        chat("legacy", "a1", "a1", "round_done", round=1),
        chat("err", "a1", "a1", "skipped", skip_reason="error", round=1, **due),
    ]
    campaign_id, ctx = await setup(("a1",), chats, loop=False)

    moved = await _schedule.promote(ctx, await repository.list_targets(campaign_id))

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    # One round only: the chats that rested are done and the error skip is not repeated.
    assert moved == 2
    assert (rows["last"].state, rows["legacy"].state) == ("done", "done")
    assert (rows["err"].state, rows["err"].skip_reason) == ("skipped", "error")
    assert _state.rest_started(campaign_id, "last") is None


@pytest.mark.asyncio
async def test_the_campaign_round_follows_the_least_advanced_unfinishedchat(
    clock: Clock,
) -> None:
    chats = [
        chat("slow", "a1", "a1", "writing", round=2),
        chat("quick", "a1", "a1", "round_done", round=5, next_action_unix=clock.now() + 600),
        chat("done", "a1", "a1", "done", round=7),
    ]
    campaign_id, ctx = await setup(("a1",), chats)

    await _schedule.tick(ctx)
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.round == 2

    await repository.update_target(campaign_id, "slow", state="done")
    await _schedule.tick(ctx, force=True)
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.round == 5
    targets: list[TargetRecord] = await repository.list_targets(campaign_id)
    assert _schedule.current_round(targets, 1) == 5


@pytest.mark.asyncio
async def test_a_group_known_by_its_id_alone_is_not_given_to_an_account_outside_it() -> None:
    chats = [
        chat(f"own{index}", "a1", "a1", kind="own", username=None, peer_id=500 + index)
        for index in range(4)
    ]
    campaign_id, ctx = await setup(("a1", "a2"), chats)

    # a2 has no way into any of them: moving one would only lose it.
    assert await _balance(campaign_id, ctx) == 0
    assert set((await _owners(campaign_id)).values()) == {"a1"}

    # With a username to join by, the same chats are as movable as any other.
    for index in range(4):
        await repository.update_target(campaign_id, f"own{index}", username=f"own_{index}")
    assert await _balance(campaign_id, ctx) == 2
    assert await _load(campaign_id, "a1", "a2") == [2, 2]


@pytest.mark.asyncio
async def test_the_balance_never_hands_a_chat_back_to_the_account_that_was_banned_in_it() -> None:
    chats = [chat(f"chat{index}", "a2", "a2") for index in range(4)]
    campaign_id, ctx = await setup(("a1", "a2"), chats)
    banned = await repository.fetch_target(campaign_id, "chat0")
    assert banned is not None
    # a1 was banned in chat0 and the chat went to a2 ...
    await _rotation.reroute(ctx, "a1", banned, "banned")
    # ... and the process restarted: nothing in memory remembers it.
    _state.reset_for_tests()

    await _balance(campaign_id, ctx)

    # The balance gives a1 other chats, not that one.
    owners = await _owners(campaign_id)
    assert owners["chat0"] == "a2"
    assert [k for k, v in owners.items() if v == "a1"]
    refused = await repository.fetch_target(campaign_id, "chat0")
    assert refused is not None
    assert refused.refused_by == ["a1"]
