"""Chats keep their own rounds: nothing waits for the slowest chat, and nobody sits idle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastSettingsUpdate
from services import chat_broadcast as service
from services.chat_broadcast import _moves, _runtime
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed
from tests.services.chat_broadcast.layout import SendLog

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_HOUR = 3600


async def _peer_ids(campaign_id: str) -> dict[str, str]:
    return {t.chat_key: str(t.peer_id) for t in await repository.list_targets(campaign_id)}


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_accounts_back_from_a_long_rest_get_chats_back_and_write(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The incident: three accounts rest a long time and the fourth carries every chat.

    The three come back and the load is shared again, instead of them idling for hours.
    """
    log = SendLog(monkeypatch, clock)
    campaign_id = await seed(
        accounts=("a1", "a2", "a3", "a4"),
        targets=tuple(f"@chat{index}" for index in range(6)),
        messages=("Hi",),
        loop=True,
        rounds=0,
        stop_mode="time",
        stop_hours=12,
        account_limit=False,
        rest_minutes={"min": 30, "max": 30},
        between_chats={"min": 30, "max": 30},
    )
    started = clock.now()
    wake = started + 6 * _HOUR
    for account_id in ("a1", "a2", "a3"):
        await repository.rest_account(campaign_id, account_id, wake, "day_limit")

    await run_to_end(campaign_id)

    before = {account for at, account, _chat in log.sends if at < wake}
    after = {
        account: [at for at, who, _c in log.sends if who == account and at >= wake]
        for account in ("a1", "a2", "a3", "a4")
    }
    assert before == {"a4"}
    # Every one of the four writes again, and soon: not hours after it was back.
    assert all(times for times in after.values())
    assert max(times[0] for times in after.values()) - wake < 2 * _HOUR
    # The chats are spread: nobody holds more than one more than anybody else.
    loads = _moves.open_load(await repository.list_targets(campaign_id))
    counts = [loads[account] for account in ("a1", "a2", "a3", "a4")]
    assert sorted(counts) == [1, 1, 2, 2]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"


async def _fast_chat_goes_on_while_the_slow_one_waits(
    campaign_id: str, log: SendLog, slow_first_write: int | None = None
) -> None:
    peers = await _peer_ids(campaign_id)
    times = log.by_chat(peers)
    # Three rounds of the quick chat came and went before the slow one wrote at all.
    assert len(times["fast"]) == 3
    assert times["fast"][-1] < times["slow"][0]
    if slow_first_write is not None:
        assert times["slow"][0] >= slow_first_write
    states = {t.chat_key: (t.state, t.round) for t in await repository.list_targets(campaign_id)}
    assert states == {"slow": ("done", 3), "fast": ("done", 3)}


async def _slow_and_fast(**overrides: object) -> str:
    return await seed(
        accounts=("a1",),
        targets=("@slow", "@fast"),
        messages=("Hi",),
        loop=True,
        rounds=3,
        rest_minutes={"min": 1, "max": 1},
        between_chats={"min": 5, "max": 5},
        **overrides,
    )


@pytest.mark.asyncio
async def test_a_chat_still_in_its_post_join_pause_does_not_hold_the_others_back(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SendLog(monkeypatch, clock)
    campaign_id = await _slow_and_fast(join_delay_minutes=120)
    # Already inside the quick chat: no join, no pause.
    telegram.members.add(("a1", "fast"))
    started = clock.now()

    await run_to_end(campaign_id)

    await _fast_chat_goes_on_while_the_slow_one_waits(campaign_id, log, started + 2 * _HOUR)


@pytest.mark.asyncio
async def test_a_pending_join_request_does_not_hold_the_others_back(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SendLog(monkeypatch, clock)
    campaign_id = await _slow_and_fast()
    telegram.members.add(("a1", "fast"))
    telegram.join_answers[("a1", "slow")] = [refused(error_type="InviteRequestSentError")]
    telegram.hidden.add("slow")
    approved = clock.now() + 3 * _HOUR
    telegram.approve_at["slow"] = approved

    await run_to_end(campaign_id)

    await _fast_chat_goes_on_while_the_slow_one_waits(campaign_id, log, approved)


@pytest.mark.asyncio
async def test_a_chat_in_slow_mode_does_not_hold_the_others_back(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SendLog(monkeypatch, clock)
    campaign_id = await _slow_and_fast()
    telegram.members.update({("a1", "fast"), ("a1", "slow")})
    # The first send, into the first chat, is refused for two hours of slow mode.
    telegram.send_answers["a1"] = [refused("slow_mode_wait", flood_wait_seconds=2 * _HOUR)]
    started = clock.now()

    await run_to_end(campaign_id)

    await _fast_chat_goes_on_while_the_slow_one_waits(campaign_id, log, started + 2 * _HOUR)


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_with_a_rest_shorter_than_the_pause_no_chat_is_starved(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = SendLog(monkeypatch, clock)
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@chat0", "@chat1", "@chat2", "@chat3"),
        messages=("Hi",),
        loop=True,
        rounds=3,
        rest_minutes={"min": 1, "max": 1},
        between_chats={"min": 120, "max": 120},
        join_delay_minutes=0,
    )

    await run_to_end(campaign_id)

    peers = await _peer_ids(campaign_id)
    order = {peer: key for key, peer in peers.items()}
    # The first chat is due again after a minute, long before the last has had its first
    # turn: the lowest round goes first, so every round goes through all four in turn.
    assert [order[chat] for _at, _account, chat in log.sends] == [
        "chat0",
        "chat1",
        "chat2",
        "chat3",
    ] * 3


@pytest.mark.asyncio
async def test_the_time_budget_ends_the_run_while_every_chat_rests(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=0,
        stop_mode="time",
        stop_hours=1,
        rest_minutes={"min": 120, "max": 120},
    )
    started = clock.now()

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    # Over within a poll of the hour, not at the end of the two-hour rests.
    assert _HOUR <= clock.now() - started <= _HOUR + 2 * settings.chat_broadcast.idle_poll_seconds


@pytest.mark.asyncio
async def test_a_stop_during_the_rests_is_continued_with_their_times_kept(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 60, "max": 60},
    )
    assert await service.start_campaign(campaign_id)
    chats = await repository.list_targets(campaign_id)
    while not chats or any(t.state != "round_done" for t in chats):
        await asyncio.sleep(0)
        chats = await repository.list_targets(campaign_id)
    ends = {t.chat_key: t.next_action_unix for t in await repository.list_targets(campaign_id)}

    assert await service.stop_campaign(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "stopped"
    kept = {t.chat_key: t.next_action_unix for t in await repository.list_targets(campaign_id)}
    assert kept == ends
    assert all(end is not None for end in kept.values())
    # Continued long after the rests ran out: the overdue chats go on at once.
    clock.advance(3 * _HOUR)
    resumed = clock.now()
    await run_to_end(campaign_id)

    assert len(telegram.sent) == 4
    assert clock.now() - resumed < 10 * 60
    final = await repository.fetch_campaign(campaign_id)
    assert final is not None
    assert final.status == "done"
    assert _runtime._TASKS.get(campaign_id) is None


@pytest.mark.asyncio
async def test_a_chat_an_error_skipped_comes_back_in_its_next_round_after_its_rest(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 10, "max": 10},
    )
    telegram.send_answers["a1"] = [refused(error_type="RPCError")]
    started = clock.now()

    await run_to_end(campaign_id)

    # alpha failed in round 1, was skipped, rested like any chat, and wrote in round 2.
    targets = {t.chat_key: (t.state, t.round) for t in await repository.list_targets(campaign_id)}
    assert targets == {"alpha": ("done", 2), "beta": ("done", 2)}
    assert len(telegram.sent) == 3
    assert clock.now() - started >= 10 * 60


@pytest.mark.asyncio
async def test_a_chat_added_on_continue_plays_all_its_own_rounds_from_the_first(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=3,
        rest_minutes={"min": 1, "max": 1},
    )
    await run_to_end(campaign_id)
    # As if Stop had caught the run in round 3 and the operator then added a chat.
    await repository.set_status(campaign_id, "stopped", round=3, finished_unix=1)
    await repository.update_target(campaign_id, "alpha", state="round_done", next_action_unix=1)
    await repository.update_target(campaign_id, "beta", state="round_done", next_action_unix=1)
    current = await service.load_settings(campaign_id)
    assert current is not None
    settings_with_gamma = current.settings.model_copy(
        update={"targets": [*current.settings.targets, "@gamma"], "rounds": 4}
    )
    await service.save_settings(
        campaign_id,
        ChatBroadcastSettingsUpdate(
            expected_updated_at=current.updated_at,
            name=current.name,
            account_ids=current.account_ids,
            settings=settings_with_gamma,
        ),
    )
    telegram.joins.clear()

    await run_to_end(campaign_id)

    rows = [r for r in await repository.list_journal(campaign_id) if r.chat_key == "gamma"]
    # Joined like any new chat, and written in rounds 1 to 4 — not dropped into round 3.
    assert ("a1", "gamma") in telegram.joins
    assert sorted(row.round for row in rows) == [1, 2, 3, 4]
