"""The board by chat, and the manual actions on it."""

from __future__ import annotations

import pytest

from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastTargetAction
from schemas.chat_broadcast_records import TargetSeed
from services import chat_broadcast as service
from services.chat_broadcast._errors import ChatBroadcastConflictError, ChatBroadcastInvalidError
from tests.services.chat_broadcast.fakes import Clock, FakeTelegram, refused, run_to_end, seed


def _seed(key: str, *, member: str | None = None, account: str = "a1") -> TargetSeed:
    return TargetSeed(
        chat_key=key,
        raw=f"@{key}",
        kind="public",
        username=key,
        assigned_account_id=account,
        member_account_id=member,
    )


@pytest.mark.asyncio
async def test_a_draft_board_has_no_rows() -> None:
    campaign_id = await seed()

    board = await service.load_board(campaign_id)

    assert board is not None
    assert (board.phase, board.rows, board.chain_length) == ("draft", [], 2)
    assert board.counters.planned == 0
    assert await service.load_board("missing") is None


@pytest.mark.asyncio
async def test_a_finished_board_has_history_counters_and_no_active_rows(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=("@alpha", "@beta"))
    telegram.send_answers["a2"] = [refused(error_type="ChatWriteForbiddenError")]

    await run_to_end(campaign_id)
    board = await service.load_board(campaign_id)

    assert board is not None
    assert board.phase == "done"
    rows = {row.chat_key: row for row in board.rows}
    assert (rows["alpha"].sent_total, rows["alpha"].planned_total) == (2, 2)
    assert rows["alpha"].last_text == "Second"
    assert [entry.kind for entry in rows["alpha"].history][:2] == ["sent", "sent"]
    assert (rows["beta"].state, rows["beta"].skip_reason) == ("skipped", "admin_only")
    assert not any(row.active for row in board.rows)
    counters = board.counters
    assert (counters.chats, counters.sent, counters.skipped, counters.joined) == (2, 2, 1, 2)


@pytest.mark.asyncio
async def test_phases_while_running(clock: Clock) -> None:
    campaign_id = await seed()
    await repository.set_status(campaign_id, "running", run_id="r1", round=1)
    await repository.replace_targets(campaign_id, [_seed("alpha")])

    joining = await service.load_board(campaign_id)
    await repository.update_target(campaign_id, "alpha", member_account_id="a1", state="waiting")
    running = await service.load_board(campaign_id)
    await repository.set_runtime(campaign_id, rest_until_unix=clock.now() + 600)
    resting = await service.load_board(campaign_id)

    assert [b.phase for b in (joining, running, resting) if b] == [
        "joining",
        "running",
        "resting",
    ]


@pytest.mark.asyncio
async def test_write_now_skip_and_hand(clock: Clock) -> None:
    campaign_id = await seed(accounts=("a1", "a2"))
    await repository.replace_targets(
        campaign_id, [_seed("alpha", member="a1"), _seed("beta"), _seed("gamma")]
    )
    await repository.update_target(
        campaign_id, "alpha", state="waiting", next_action_unix=clock.now() + 3600
    )

    await service.act_on_target(
        campaign_id, ChatBroadcastTargetAction(chat_key="alpha", action="now")
    )
    await service.act_on_target(
        campaign_id, ChatBroadcastTargetAction(chat_key="beta", action="skip")
    )
    await service.act_on_target(
        campaign_id, ChatBroadcastTargetAction(chat_key="gamma", action="hand", account_id="a2")
    )

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert (rows["alpha"].next_action_unix or 0) <= clock.now()
    assert (rows["beta"].state, rows["beta"].skip_reason) == ("skipped", "manual")
    assert (rows["gamma"].assigned_account_id, rows["gamma"].handed_from_account_id) == (
        "a2",
        "a1",
    )
    kinds = {(e.chat_key, e.kind) for e in await repository.list_events(campaign_id)}
    assert {("beta", "skipped"), ("gamma", "handed")} <= kinds


@pytest.mark.asyncio
async def test_actions_that_lost_the_race_answer_conflict() -> None:
    campaign_id = await seed(accounts=("a1", "a2"))
    await repository.replace_targets(campaign_id, [_seed("alpha"), _seed("beta")])
    await repository.update_target(campaign_id, "beta", state="joining")

    with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
        await service.act_on_target(
            campaign_id, ChatBroadcastTargetAction(chat_key="alpha", action="now")
        )
    with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
        await service.act_on_target(
            campaign_id, ChatBroadcastTargetAction(chat_key="beta", action="hand", account_id="a2")
        )
    with pytest.raises(ChatBroadcastConflictError, match="target_not_found"):
        await service.act_on_target(
            campaign_id, ChatBroadcastTargetAction(chat_key="nope", action="skip")
        )
    with pytest.raises(ChatBroadcastInvalidError, match="account_not_in_campaign"):
        await service.act_on_target(
            campaign_id, ChatBroadcastTargetAction(chat_key="alpha", action="hand", account_id="x")
        )
    await service.act_on_target(
        campaign_id, ChatBroadcastTargetAction(chat_key="alpha", action="skip")
    )
    with pytest.raises(ChatBroadcastConflictError, match="target_state_changed"):
        await service.act_on_target(
            campaign_id, ChatBroadcastTargetAction(chat_key="alpha", action="skip")
        )
    assert not await service.act_on_target(
        "missing", ChatBroadcastTargetAction(chat_key="alpha", action="skip")
    )
