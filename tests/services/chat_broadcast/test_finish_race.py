"""Finishing without losing a chat: the run's finish against a keep, in both orders."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastTargetAction
from schemas.chat_broadcast_records import TargetSeed
from services import chat_broadcast as service
from services.chat_broadcast import _coordinator, _schedule, _seams
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import CampaignRecord, TargetRecord
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


def _keep(chat_key: str = "alpha") -> ChatBroadcastTargetAction:
    return ChatBroadcastTargetAction(chat_key=chat_key, action="keep")


def _seed(key: str) -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        peer_id=7,
        assigned_account_id="a1",
        member_account_id="a1",
    )


async def _target(campaign_id: str, chat_key: str = "alpha") -> TargetRecord:
    target = await repository.fetch_target(campaign_id, chat_key)
    assert target is not None
    return target


async def _running_with_a_skipped_chat() -> str:
    campaign_id = await seed(accounts=("a1",))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta")])
    await repository.set_status(campaign_id, "running", run_id="run-1", round=2)
    await repository.update_target(
        campaign_id, "alpha", state="skipped", skip_reason="deleted", round=2
    )
    await repository.update_target(campaign_id, "beta", state="done", round=2)
    return campaign_id


@pytest.mark.asyncio
async def test_a_finish_that_lands_between_the_keeps_read_and_write_leaves_the_flag_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await _running_with_a_skipped_chat()
    real = repository.keep_target
    finished: list[bool] = []

    async def finish_first(seen: CampaignRecord, chat_key: str, **fields: object) -> bool:
        # The keep read "running"; the run closes before its write.
        if not finished:
            finished.append(
                await repository.try_finish(
                    campaign_id,
                    work_left=lambda chats: _schedule.work_left(chats, 2),
                    now_unix=_seams.now(),
                )
            )
        return await real(seen, chat_key, **fields)

    monkeypatch.setattr(repository, "keep_target", finish_first)

    await service.act_on_target(campaign_id, _keep())

    assert finished == [True]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    # Nothing is left queued in a finished campaign: the chat is flagged, not lost in limbo.
    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.ignore_deleted) == (
        "skipped",
        "deleted",
        True,
    )
    board = await service.load_board(campaign_id)
    assert board is not None
    assert not any(row.active for row in board.rows)


@pytest.mark.asyncio
async def test_a_keep_that_lands_first_keeps_the_run_from_finishing() -> None:
    campaign_id = await _running_with_a_skipped_chat()

    await service.act_on_target(campaign_id, _keep())

    assert not await repository.try_finish(
        campaign_id, work_left=lambda chats: _schedule.work_left(chats, 2), now_unix=_seams.now()
    )
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "running"
    assert (await _target(campaign_id)).state == "queued"


@pytest.mark.asyncio
async def test_a_chat_kept_as_the_last_worker_leaves_is_played_before_the_run_ends(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.deleted.add(101)
    real = _coordinator._finish
    kept: list[bool] = []

    async def keep_then_finish(ctx: RunContext) -> bool:
        # Everything is through but the chat round 2 skipped for deletions; the operator
        # keeps it just as the run goes to close.
        if not kept:
            kept.append(await service.act_on_target(campaign_id, _keep("alpha")))
        return await real(ctx)

    monkeypatch.setattr(_coordinator, "_finish", keep_then_finish)

    await run_to_end(campaign_id)

    assert kept == [True]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    # Round 1 wrote both chats, round 2 wrote beta, and the kept chat was written in its own.
    assert len(telegram.sent) == 4
    target = await _target(campaign_id)
    assert (target.state, target.round, target.ignore_deleted) == ("done", 2, True)


@pytest.mark.asyncio
async def test_a_run_without_a_round_limit_never_finishes_while_a_chat_rests(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=0,
        stop_messages=3,
        rest_minutes={"min": 1, "max": 1},
    )

    await run_to_end(campaign_id)

    # Three rounds of one chat: the only end is the operator's volume.
    assert len(telegram.sent) == 3
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    [target] = await repository.list_targets(campaign_id)
    assert (target.state, target.round) == ("round_done", 3)


@pytest.mark.asyncio
async def test_a_run_does_not_end_while_a_chat_is_resting_for_its_next_round(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=2,
        rest_minutes={"min": 30, "max": 30},
    )
    started = clock.now()

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2
    # The second round came only after the whole rest, which nothing cut short.
    assert clock.now() - started >= 30 * 60
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
