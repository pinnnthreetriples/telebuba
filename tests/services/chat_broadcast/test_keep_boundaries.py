"""«Писать всё равно» landing exactly on a round boundary the engine is crossing.

The keep decides from what it read, the engine moves on in between; each test lands the
keep at one such point, through the repository call or engine step that crosses it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastTargetAction
from schemas.chat_broadcast_records import TargetSeed
from services import chat_broadcast as service
from services.chat_broadcast import _coordinator
from services.chat_broadcast._errors import ChatBroadcastConflictError
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import FakeTelegram


def _keep(chat_key: str = "alpha") -> ChatBroadcastTargetAction:
    return ChatBroadcastTargetAction(chat_key=chat_key, action="keep")


async def _campaign(telegram: FakeTelegram, *, targets: tuple[str, ...], rounds: int) -> str:
    """Round 1 writes every chat; an admin deletes alpha's message, so round 2 skips it."""
    campaign_id = await seed(
        accounts=("a1",),
        targets=targets,
        messages=("Hi",),
        loop=True,
        rounds=rounds,
        rest_minutes={"min": 1, "max": 1},
    )
    telegram.deleted.add(101)
    return campaign_id


async def _target(campaign_id: str, chat_key: str = "alpha") -> TargetRecord:
    target = await repository.fetch_target(campaign_id, chat_key)
    assert target is not None
    return target


def _stale_once(monkeypatch: pytest.MonkeyPatch, open_key: str) -> None:
    """The keep's next read of the chats sees ``open_key`` still at work, as a moment ago."""
    real = repository.list_targets

    async def stale(campaign_id: str) -> list[TargetRecord]:
        monkeypatch.setattr(repository, "list_targets", real)
        return [
            t.model_copy(update={"state": "waiting"}) if t.chat_key == open_key else t
            for t in await real(campaign_id)
        ]

    monkeypatch.setattr(repository, "list_targets", stale)


def _on_round_end(
    monkeypatch: pytest.MonkeyPatch, round_number: int, act: Callable[[], Awaitable[None]]
) -> None:
    """Run ``act`` once every worker of ``round_number`` has left, before the round closes."""
    real = _coordinator._round_open

    async def round_open(ctx: RunContext, number: int) -> bool:
        if number == round_number:
            monkeypatch.setattr(_coordinator, "_round_open", real)
            await act()
        return await real(ctx, number)

    monkeypatch.setattr(_coordinator, "_round_open", round_open)


@pytest.mark.asyncio
async def test_a_keep_that_saw_the_round_open_does_not_stall_it_once_it_closed(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await _campaign(telegram, targets=("@alpha", "@beta"), rounds=3)

    async def keep() -> None:
        # Read while beta was still being written; lands after the last worker left.
        _stale_once(monkeypatch, "beta")
        await service.act_on_target(campaign_id, _keep())

    _on_round_end(monkeypatch, 2, keep)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    # Round 2 wrote only beta; the kept chat is back from round 3.
    assert len(telegram.sent) == 2 + 1 + 2
    target = await _target(campaign_id)
    assert (target.state, target.round, target.ignore_deleted) == ("done", 3, True)


@pytest.mark.asyncio
async def test_the_only_chat_kept_as_its_round_closes_still_gets_the_next_round(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await _campaign(telegram, targets=("@alpha",), rounds=3)

    async def keep() -> None:
        await service.act_on_target(campaign_id, _keep())

    _on_round_end(monkeypatch, 2, keep)

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2
    target = await _target(campaign_id)
    assert (target.state, target.round) == ("done", 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("after", [True, False])
async def test_a_chat_kept_as_the_next_round_opens_is_written_in_it(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch, *, after: bool
) -> None:
    campaign_id = await _campaign(telegram, targets=("@alpha", "@beta"), rounds=3)
    real = repository.start_round

    async def start_round(campaign: str, round_number: int) -> int:
        # Round 2 skips alpha; the keep lands as round 3 opens.
        if round_number == 3 and not after:
            await service.act_on_target(campaign_id, _keep())
        requeued = await real(campaign, round_number)
        if round_number == 3 and after:
            await service.act_on_target(campaign_id, _keep())
        return requeued

    monkeypatch.setattr(repository, "start_round", start_round)

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 2 + 1 + 2
    rows = [r for r in await repository.list_journal(campaign_id) if r.chat_key == "alpha"]
    assert sorted((r.round, r.status) for r in rows) == [(1, "deleted"), (3, "sent")]


@pytest.mark.asyncio
async def test_a_chat_kept_as_the_last_round_closes_stays_skipped_with_the_flag(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await _campaign(telegram, targets=("@alpha", "@beta"), rounds=2)
    real = repository.finish_rounds

    async def finish_rounds(campaign: str) -> None:
        await real(campaign)
        await service.act_on_target(campaign_id, _keep())

    monkeypatch.setattr(repository, "finish_rounds", finish_rounds)

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    target = await _target(campaign_id)
    assert (target.state, target.skip_reason, target.ignore_deleted) == (
        "skipped",
        "deleted",
        True,
    )
    board = await service.load_board(campaign_id)
    assert board is not None
    assert not any(row.active for row in board.rows)


def _seed(key: str) -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        assigned_account_id="a1",
        member_account_id="a1",
    )


@pytest.mark.asyncio
async def test_a_keep_that_keeps_reading_a_round_that_is_over_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = await seed(accounts=("a1",))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta")])
    await repository.set_status(campaign_id, "running", run_id="run-1", round=2)
    await repository.update_target(campaign_id, "alpha", state="skipped", skip_reason="deleted")
    await repository.update_target(campaign_id, "beta", state="round_done", round=2)
    real = repository.list_targets

    async def stale(campaign: str) -> list[TargetRecord]:
        return [
            t.model_copy(update={"state": "waiting"}) if t.chat_key == "beta" else t
            for t in await real(campaign)
        ]

    monkeypatch.setattr(repository, "list_targets", stale)

    with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
        await service.act_on_target(campaign_id, _keep())

    target = await _target(campaign_id)
    assert (target.state, target.ignore_deleted) == ("skipped", False)


@pytest.mark.asyncio
async def test_the_kept_write_holds_only_on_the_campaign_moment_it_was_decided_on() -> None:
    campaign_id = await seed(accounts=("a1",))
    await repository.replace_targets(campaign_id, [_seed("alpha")])
    await repository.set_status(campaign_id, "running", run_id="run-1", round=2)
    await repository.update_target(campaign_id, "alpha", state="skipped", skip_reason="deleted")

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    for moved in ({"status": "stopped"}, {"round": 1}, {"rest_until_unix": 5}):
        assert not await repository.keep_target(record.model_copy(update=moved), "alpha")
    assert not (await _target(campaign_id)).ignore_deleted

    assert await repository.keep_target(record, "alpha")
    assert (await _target(campaign_id)).ignore_deleted
