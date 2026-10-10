"""«Писать всё равно»: a chat skipped for deleted messages goes back to work, until a ban."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from core.repositories.logs import list_recent_logs
from schemas.chat_broadcast import ChatBroadcastTargetAction
from schemas.chat_broadcast_records import TargetSeed
from services import _account_owner
from services import chat_broadcast as service
from services.chat_broadcast import _seams
from services.chat_broadcast._errors import ChatBroadcastConflictError
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import JournalRecord, TargetRecord
    from schemas.telegram_actions import ActionResult, TelegramAction
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram


def _keep(chat_key: str) -> ChatBroadcastTargetAction:
    return ChatBroadcastTargetAction(chat_key=chat_key, action="keep")


def _seed(key: str, account: str = "a1") -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        assigned_account_id=account,
        member_account_id=account,
    )


async def _target(campaign_id: str, chat_key: str = "alpha") -> TargetRecord:
    target = await repository.fetch_target(campaign_id, chat_key)
    assert target is not None
    return target


async def _deleted_out_of_round_two(telegram: FakeTelegram, rounds: int = 3) -> str:
    """A finished run whose one chat an admin cleaned in round 1, so round 2 skipped it."""
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha",),
        messages=("Hi",),
        loop=True,
        rounds=rounds,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.deleted.add(101)
    await run_to_end(campaign_id)
    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.round) == ("skipped", "deleted", 2)
    return campaign_id


def _journal_by_round(rows: list[JournalRecord]) -> list[tuple[int, str]]:
    return sorted((row.round, row.status) for row in rows)


@pytest.mark.asyncio
async def test_a_kept_chat_of_a_stopped_run_is_written_on_continue_despite_new_deletions(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await _deleted_out_of_round_two(telegram)
    # As if Stop had caught the run in round 2.
    await repository.set_status(campaign_id, "stopped")

    await service.act_on_target(campaign_id, _keep("alpha"))

    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.round, target.step_index) == (
        "queued",
        None,
        2,
        0,
    )
    assert target.ignore_deleted
    logs = [r for r in await list_recent_logs(limit=50) if r.event == "chat_broadcast_chat_kept"]
    assert [(r.level, r.extra) for r in logs] == [
        ("INFO", {"campaign_id": campaign_id, "chat": "@alpha"})
    ]
    board = await service.load_board(campaign_id)
    assert board is not None
    [row] = board.rows
    assert (row.ignore_deleted, row.active) == (True, True)

    # The admin deletes the round-2 message too; round 3 writes all the same.
    telegram.deleted.add(102)
    await run_to_end(campaign_id)

    assert telegram.texts() == ["Hi", "Hi", "Hi"]
    rows = await repository.list_journal(campaign_id)
    assert _journal_by_round(rows) == [(1, "deleted"), (2, "deleted"), (3, "sent")]
    target = await _target(campaign_id)
    assert (target.state, target.round, target.message_deleted) == ("done", 3, True)
    kinds = [(e.round, e.kind) for e in await repository.list_events(campaign_id)]
    assert (3, "deleted") in kinds


@pytest.mark.asyncio
async def test_a_kept_chat_of_a_finished_campaign_is_written_by_the_next_run(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await _deleted_out_of_round_two(telegram, rounds=2)

    await service.act_on_target(campaign_id, _keep("alpha"))

    # This run is over: the row keeps saying why it was skipped; the flag is what counts.
    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.ignore_deleted) == (
        "skipped",
        "deleted",
        True,
    )

    # The next run lays the chats out again and carries the choice over; its round-1
    # message (102) is deleted as well, and round 2 writes anyway.
    telegram.deleted.add(102)
    await run_to_end(campaign_id)

    target = await _target(campaign_id)
    assert (target.state, target.round, target.ignore_deleted) == ("done", 2, True)
    assert len(telegram.sent) == 1 + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("resting", [True, False])
async def test_a_chat_kept_between_rounds_waits_for_the_next_one(
    telegram: FakeTelegram, clock: Clock, *, resting: bool
) -> None:
    campaign_id = await _deleted_out_of_round_two(telegram)
    # Running, and either resting or with every chat of round 2 already through.
    rest = clock.now() + 600 if resting else None
    await repository.set_status(campaign_id, "running", rest_until_unix=rest)

    await service.act_on_target(campaign_id, _keep("alpha"))

    # It only takes the flag: the round may be closing; the next one re-queues it.
    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.ignore_deleted) == (
        "skipped",
        "deleted",
        True,
    )
    # The board shows it among the chats still at work, not as skipped for good.
    board = await service.load_board(campaign_id)
    assert board is not None
    assert [(row.chat_key, row.active) for row in board.rows if row.chat_key == "alpha"] == [
        ("alpha", True)
    ]
    assert await repository.start_round(campaign_id, 3) == 1
    target = await _target(campaign_id)
    assert (target.state, target.round, target.step_index) == ("queued", 3, 0)


@pytest.mark.asyncio
async def test_a_chat_kept_while_its_round_runs_is_written_in_it(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        loop=True,
        rounds=3,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.deleted.add(101)
    original = telegram.execute
    kept: list[str] = []

    async def execute(account_id: str, action: TelegramAction) -> ActionResult:
        if not kept:
            skipped = [
                t for t in await repository.list_targets(campaign_id) if t.skip_reason == "deleted"
            ]
            if skipped:
                kept.append(skipped[0].chat_key)
                await service.act_on_target(campaign_id, _keep(skipped[0].chat_key))
                target = await _target(campaign_id, skipped[0].chat_key)
                assert (target.state, target.round) == ("queued", 2)
                assert target.next_action_unix == _seams.now()
                # Its round-1 message was deleted; so will its round-2 one be (101 and
                # 102 went out in round 1, 103 is the other chat's round-2 message).
                telegram.deleted.add(104)
        return await original(account_id, action)

    monkeypatch.setattr(_seams, "execute", execute)

    await run_to_end(campaign_id)

    assert len(kept) == 1
    assert len(telegram.sent) == 2 * 3
    target = await _target(campaign_id, kept[0])
    assert (target.state, target.round, target.ignore_deleted) == ("done", 3, True)
    rows = [r for r in await repository.list_journal(campaign_id) if r.chat_key == kept[0]]
    assert _journal_by_round(rows) == [(1, "deleted"), (2, "deleted"), (3, "sent")]


@pytest.mark.asyncio
async def test_a_kept_chat_whose_account_dropped_out_goes_to_a_live_one() -> None:
    campaign_id = await seed(accounts=("a1", "a2"))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta", "a2")])
    await repository.set_status(campaign_id, "running", run_id="run-1", round=2)
    await repository.update_target(campaign_id, "alpha", state="skipped", skip_reason="deleted")
    await repository.update_target(campaign_id, "beta", round=2)
    assert _account_owner.try_claim("a2", "chat_broadcast", campaign_id) is None

    await service.act_on_target(campaign_id, _keep("alpha"))

    target = await _target(campaign_id)
    assert (target.state, target.round) == ("queued", 2)
    assert (target.assigned_account_id, target.handed_from_account_id) == ("a2", "a1")


@pytest.mark.asyncio
async def test_keep_is_refused_for_a_chat_not_skipped_for_deletions() -> None:
    campaign_id = await seed(accounts=("a1",))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta")])
    await repository.set_status(campaign_id, "stopped", run_id="run-1", round=2)
    await repository.update_target(campaign_id, "alpha", state="skipped", skip_reason="banned")

    for chat_key in ("alpha", "beta"):
        with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
            await service.act_on_target(campaign_id, _keep(chat_key))

    assert not (await _target(campaign_id)).ignore_deleted
    assert not (await _target(campaign_id, "beta")).ignore_deleted


@pytest.mark.asyncio
async def test_a_ban_still_skips_a_kept_chat(telegram: FakeTelegram) -> None:
    campaign_id = await _deleted_out_of_round_two(telegram)
    await repository.set_status(campaign_id, "stopped")
    await service.act_on_target(campaign_id, _keep("alpha"))
    telegram.send_answers["a1"] = [refused(error_type="ChatWriteForbiddenError")]

    await run_to_end(campaign_id)

    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.ignore_deleted) == (
        "skipped",
        "admin_only",
        True,
    )
    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_a_fresh_layout_carries_the_flag_by_chat_key() -> None:
    campaign_id = await seed(accounts=("a1",))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta")])
    await repository.update_target(campaign_id, "alpha", ignore_deleted=1)

    await repository.replace_targets(campaign_id, [_seed("gamma"), _seed("alpha")])

    flags = {t.chat_key: t.ignore_deleted for t in await repository.list_targets(campaign_id)}
    assert flags == {"gamma": False, "alpha": True}

    await repository.merge_targets(campaign_id, [_seed("delta")], round_number=1, removed=set())

    flags = {t.chat_key: t.ignore_deleted for t in await repository.list_targets(campaign_id)}
    assert flags == {"gamma": False, "alpha": True, "delta": False}
