"""Run lifecycle: start refusals, busy accounts, stop and continue, restart, shutdown."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastSettingsUpdate
from schemas.chat_broadcast_records import JournalKey, TargetSeed
from services import _account_owner
from services import chat_broadcast as service
from services.chat_broadcast import _runtime
from services.chat_broadcast._errors import ChatBroadcastConflictError
from tests.services.chat_broadcast.fakes import run_to_end, seed

if TYPE_CHECKING:
    from tests.services.chat_broadcast.fakes import FakeTelegram


async def _status(campaign_id: str) -> str:
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    return record.status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"messages": ("  ",)}, "no_messages"),
        ({"targets": ()}, "no_targets"),
    ],
)
async def test_start_refuses_what_cannot_run(
    telegram: FakeTelegram, overrides: dict[str, Any], code: str
) -> None:
    campaign_id = await seed(**overrides)

    with pytest.raises(ChatBroadcastConflictError, match=code):
        await service.start_campaign(campaign_id)
    assert telegram.joins == []


@pytest.mark.asyncio
async def test_start_without_accounts_and_with_every_account_busy(telegram: FakeTelegram) -> None:
    empty = await seed(accounts=())
    with pytest.raises(ChatBroadcastConflictError, match="no_accounts"):
        await service.start_campaign(empty)

    busy = await seed(accounts=("a1",))
    _account_owner.try_claim("a1", "warming", "run-1")
    with pytest.raises(ChatBroadcastConflictError, match="no_free_accounts"):
        await service.start_campaign(busy)
    assert telegram.joins == []


@pytest.mark.asyncio
async def test_a_busy_account_is_left_out_and_the_board_says_who_has_it(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"))
    _account_owner.try_claim("a2", "neuroshilling", "ns-1")

    await run_to_end(campaign_id)

    assert {account for account, _token in telegram.joins} == {"a1"}
    board = await service.load_board(campaign_id)
    assert board is not None
    accounts = {a.account_id: a for a in board.accounts}
    assert (accounts["a2"].state, accounts["a2"].busy_owner) == ("busy", "neuroshilling")
    assert _account_owner.owner_of("a1") is None


@pytest.mark.asyncio
async def test_start_refuses_a_stale_stamp_and_a_running_campaign(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    with pytest.raises(ChatBroadcastConflictError, match="campaign_changed"):
        await service.start_campaign(campaign_id, expected_updated_at="2020-01-01T00:00:00+00:00")
    assert not await service.start_campaign("missing")

    telegram.hold = asyncio.Event()
    try:
        assert await service.start_campaign(campaign_id)
        with pytest.raises(ChatBroadcastConflictError, match="campaign_running"):
            await service.start_campaign(campaign_id)
        with pytest.raises(ChatBroadcastConflictError, match="campaign_running"):
            await service.delete_campaign(campaign_id)
        assert await service.stop_campaign(campaign_id)
    finally:
        telegram.hold.set()
    assert await _status(campaign_id) == "stopped"
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_continue_keeps_the_run_and_sends_nothing_twice(telegram: FakeTelegram) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha", "@beta"), messages=("one",))
    telegram.after_first_send = asyncio.Event()
    assert await service.start_campaign(campaign_id)
    await telegram.after_first_send.wait()
    await service.stop_campaign(campaign_id)
    first = await repository.fetch_campaign(campaign_id)
    assert first is not None
    assert first.status == "stopped"

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.run_id) == ("done", first.run_id)
    assert sorted(telegram.texts()) == ["one", "one"]
    assert {row.chat_key for row in await repository.list_journal(campaign_id)} == {
        "alpha",
        "beta",
    }


@pytest.mark.asyncio
async def test_settings_cannot_change_under_a_run(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    telegram.hold = asyncio.Event()
    try:
        await service.start_campaign(campaign_id)
        current = await service.load_settings(campaign_id)
        assert current is not None
        with pytest.raises(ChatBroadcastConflictError, match="campaign_running"):
            await service.save_settings(
                campaign_id,
                ChatBroadcastSettingsUpdate(
                    expected_updated_at=current.updated_at,
                    name="x",
                    account_ids=current.account_ids,
                    settings=current.settings,
                ),
            )
    finally:
        telegram.hold.set()
        await service.stop_campaign(campaign_id)


@pytest.mark.asyncio
async def test_restart_resumes_the_same_run_and_never_repeats_a_pending_send(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("one", "two"))
    await repository.set_status(campaign_id, "running", run_id="run-1", round=1, started_unix=1)
    await repository.replace_targets(
        campaign_id,
        [
            _seed_member("alpha"),
        ],
    )
    await repository.update_target(campaign_id, "alpha", state="writing")
    await repository.claim_message(
        JournalKey(run_id="run-1", chat_key="alpha", round=1, step_index=0),
        campaign_id=campaign_id,
        account_id="a1",
        kind="text",
    )

    await service.reconcile_chat_broadcast_on_startup()
    await _runtime._TASKS[campaign_id]

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.run_id) == ("done", "run-1")
    assert record.resumed_unix is not None
    assert telegram.texts() == ["two"]
    statuses = [row.status for row in await repository.list_journal(campaign_id)]
    assert statuses == ["unconfirmed", "sent"]


def _seed_member(key: str) -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        peer_id=77,
        assigned_account_id="a1",
        member_account_id="a1",
    )


@pytest.mark.asyncio
async def test_restart_finishes_an_interrupted_stop_and_stalls_without_accounts(
    telegram: FakeTelegram,
) -> None:
    stopping = await seed(accounts=("a1",))
    await repository.set_status(stopping, "stopping", run_id="run-1")
    busy = await seed(accounts=("a2",))
    await repository.set_status(busy, "running", run_id="run-2", round=1)
    lost = await seed(accounts=("a3",))
    await repository.set_status(lost, "running", run_id=None)
    _account_owner.try_claim("a2", "warming", "w-1")

    await service.reconcile_chat_broadcast_on_startup()

    assert await _status(stopping) == "stopped"
    assert await _status(busy) == "stalled"
    assert await _status(lost) == "failed"
    assert telegram.joins == []


@pytest.mark.asyncio
async def test_shutdown_leaves_the_run_running_for_the_next_boot(telegram: FakeTelegram) -> None:
    campaign_id = await seed()
    telegram.hold = asyncio.Event()
    await service.start_campaign(campaign_id)
    await asyncio.sleep(0)
    await service.shutdown_chat_broadcast_on_shutdown()
    assert await _status(campaign_id) == "running"
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_continue_after_a_stall_with_a_new_account(telegram: FakeTelegram) -> None:
    from tests.services.chat_broadcast.fakes import refused  # noqa: PLC0415

    campaign_id = await seed(accounts=("a1", "a9"), targets=("@alpha",), messages=("Hi",))
    _account_owner.try_claim("a9", "warming", "w-1")
    telegram.send_answers["a1"] = [refused("peer_flood")]
    await run_to_end(campaign_id)
    assert await _status(campaign_id) == "stalled"

    _account_owner.release("a9", "warming", "w-1")
    await run_to_end(campaign_id)

    assert await _status(campaign_id) == "done"
    assert [s.account_id for s in telegram.sent] == ["a9"]
