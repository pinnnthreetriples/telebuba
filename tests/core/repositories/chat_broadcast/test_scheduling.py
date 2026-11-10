"""Chats keep their own rounds: promote, rest, move the rest, adopt a barrier, finish."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import create_account
from core.repositories import chat_broadcast as repository
from schemas.accounts import AccountCreate
from schemas.chat_broadcast_records import TargetSeed

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.chat_broadcast import ChatBroadcastStatus
    from schemas.chat_broadcast_records import TargetRecord

_NOW = 1_000


async def _campaign() -> str:
    for account_id in ("a1", "a2"):
        await create_account(
            AccountCreate(account_id=account_id, label=account_id, session_name=account_id)
        )
    created = await repository.create_campaign("Crypto")
    saved = await repository.save_settings(
        created.campaign_id,
        name="Crypto",
        settings_json="{}",
        account_ids=["a1", "a2"],
        expected_updated_at=created.updated_at,
    )
    assert saved is not None
    return created.campaign_id


def _seed(key: str, account: str | None = "a1") -> TargetSeed:
    return TargetSeed(chat_key=key, raw=f"@{key}", kind="public", assigned_account_id=account)


async def _target(campaign_id: str, key: str) -> TargetRecord:
    target = await repository.fetch_target(campaign_id, key)
    assert target is not None
    return target


async def _resting(campaign_id: str, key: str = "x", *, until: int | None = _NOW) -> None:
    await repository.replace_targets(campaign_id, [_seed(key)])
    await repository.update_target(
        campaign_id, key, state="round_done", step_index=2, next_action_unix=until
    )


@pytest.mark.asyncio
async def test_promote_opens_the_next_round_of_a_chat_whose_rest_is_over() -> None:
    campaign_id = await _campaign()
    await _resting(campaign_id)
    await repository.update_target(campaign_id, "x", skip_reason=None, requested_unix=5)

    assert await repository.promote_target(campaign_id, "x", round_number=1, now_unix=_NOW)

    target = await _target(campaign_id, "x")
    assert (target.state, target.round, target.step_index) == ("queued", 2, 0)
    assert (target.skip_reason, target.next_action_unix) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("until", "round_number", "now_unix"),
    [(_NOW + 1, 1, _NOW), (None, 1, _NOW), (_NOW, 2, _NOW)],
    ids=["not due", "no end yet", "another round"],
)
async def test_promote_leaves_a_chat_that_is_not_due_or_not_in_that_round(
    until: int | None, round_number: int, now_unix: int
) -> None:
    campaign_id = await _campaign()
    await _resting(campaign_id, until=until)

    assert not await repository.promote_target(
        campaign_id, "x", round_number=round_number, now_unix=now_unix
    )

    target = await _target(campaign_id, "x")
    assert (target.state, target.round) == ("round_done", 1)


@pytest.mark.asyncio
async def test_two_promotions_of_one_chat_advance_it_one_round() -> None:
    campaign_id = await _campaign()
    await _resting(campaign_id)

    first = await repository.promote_target(campaign_id, "x", round_number=1, now_unix=_NOW)
    second = await repository.promote_target(campaign_id, "x", round_number=1, now_unix=_NOW)

    assert (first, second) == (True, False)
    assert (await _target(campaign_id, "x")).round == 2


@pytest.mark.asyncio
async def test_promote_brings_back_error_skips_and_kept_chats_but_no_other_skip() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(
        campaign_id, [_seed("err"), _seed("kept"), _seed("unkept"), _seed("admin")]
    )
    for key, reason, kept in (
        ("err", "error", 0),
        ("kept", "deleted", 1),
        ("unkept", "deleted", 0),
        ("admin", "admin_only", 0),
    ):
        await repository.update_target(
            campaign_id,
            key,
            state="skipped",
            skip_reason=reason,
            ignore_deleted=kept,
            next_action_unix=_NOW,
        )

    moved = {
        key: await repository.promote_target(campaign_id, key, round_number=1, now_unix=_NOW)
        for key in ("err", "kept", "unkept", "admin")
    }

    assert moved == {"err": True, "kept": True, "unkept": False, "admin": False}
    err = await _target(campaign_id, "err")
    assert (err.state, err.skip_reason, err.round) == ("queued", None, 2)


@pytest.mark.asyncio
async def test_the_last_round_is_never_left() -> None:
    campaign_id = await _campaign()
    await _resting(campaign_id)

    assert not await repository.promote_target(
        campaign_id, "x", round_number=1, now_unix=_NOW, max_round=1
    )
    assert await repository.promote_target(
        campaign_id, "x", round_number=1, now_unix=_NOW, max_round=2
    )


@pytest.mark.asyncio
async def test_a_finished_chain_rests_only_from_writing_in_its_round() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])

    assert not await repository.rest_target(campaign_id, "x", round_number=1, until_unix=_NOW)
    await repository.update_target(campaign_id, "x", state="writing")
    assert not await repository.rest_target(campaign_id, "x", round_number=2, until_unix=_NOW)
    assert await repository.rest_target(campaign_id, "x", round_number=1, until_unix=_NOW)

    target = await _target(campaign_id, "x")
    assert (target.state, target.next_action_unix) == ("round_done", _NOW)


@pytest.mark.asyncio
async def test_a_moved_rest_never_lands_in_the_next_round() -> None:
    campaign_id = await _campaign()
    await _resting(campaign_id, until=_NOW + 600)

    assert await repository.move_target_rest(campaign_id, "x", round_number=1, until_unix=_NOW + 5)
    assert (await _target(campaign_id, "x")).next_action_unix == _NOW + 5
    assert await repository.promote_target(campaign_id, "x", round_number=1, now_unix=_NOW + 5)

    assert not await repository.move_target_rest(
        campaign_id, "x", round_number=1, until_unix=_NOW + 900
    )
    assert not await repository.move_target_rest(
        campaign_id, "x", round_number=2, until_unix=_NOW + 900
    )
    target = await _target(campaign_id, "x")
    assert (target.state, target.round, target.next_action_unix) == ("queued", 2, None)


@pytest.mark.asyncio
async def test_an_unscheduled_rest_is_only_written_where_no_end_is_set() -> None:
    campaign_id = await _campaign()
    await _resting(campaign_id, until=_NOW + 50)

    assert not await repository.move_target_rest(
        campaign_id, "x", round_number=1, until_unix=_NOW + 9, unscheduled=True
    )
    await repository.update_target(campaign_id, "x", next_action_unix=None)
    assert await repository.move_target_rest(
        campaign_id, "x", round_number=1, until_unix=_NOW + 9, unscheduled=True
    )

    assert (await _target(campaign_id, "x")).next_action_unix == _NOW + 9


@pytest.mark.asyncio
async def test_a_barrier_rest_passes_from_the_campaign_to_the_chats_waiting_for_it() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(
        campaign_id, [_seed("done"), _seed("err"), _seed("live"), _seed("dated")]
    )
    await repository.update_target(campaign_id, "done", state="round_done")
    await repository.update_target(campaign_id, "err", state="skipped", skip_reason="error")
    await repository.update_target(campaign_id, "dated", state="round_done", next_action_unix=7)
    await repository.set_runtime(campaign_id, round=3, rest_until_unix=_NOW)

    assert await repository.adopt_barrier_rest(campaign_id) == 2

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert [rows[key].next_action_unix for key in ("done", "err", "live", "dated")] == [
        _NOW,
        _NOW,
        None,
        7,
    ]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.round, record.rest_until_unix) == (3, None)
    assert await repository.adopt_barrier_rest(campaign_id) == 0


def _work_left(targets: Sequence[TargetRecord]) -> bool:
    return any(t.state in {"queued", "round_done"} for t in targets)


@pytest.mark.asyncio
async def test_a_run_finishes_only_when_no_chat_has_work_left() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y")])
    await repository.set_status(campaign_id, "running", run_id="r1", round=1)
    await repository.update_target(campaign_id, "x", state="done")

    assert not await repository.try_finish(campaign_id, work_left=_work_left, now_unix=_NOW)
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.finished_unix) == ("running", None)

    await repository.update_target(campaign_id, "y", state="skipped", skip_reason="manual")
    assert await repository.try_finish(campaign_id, work_left=_work_left, now_unix=_NOW)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.finished_unix, record.rest_until_unix) == ("done", _NOW, None)


@pytest.mark.asyncio
async def test_finishing_turns_a_leftover_rest_into_done() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    await repository.set_status(campaign_id, "running", run_id="r1", round=1)
    await repository.update_target(campaign_id, "x", state="round_done")

    assert await repository.try_finish(campaign_id, work_left=lambda _t: False, now_unix=_NOW)

    assert (await _target(campaign_id, "x")).state == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["stopping", "stopped", "draft", "done"])
async def test_only_a_running_campaign_is_finished(status: ChatBroadcastStatus) -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    await repository.update_target(campaign_id, "x", state="done")
    await repository.set_status(campaign_id, status, run_id="r1")

    assert not await repository.try_finish(campaign_id, work_left=_work_left, now_unix=_NOW)
    assert not await repository.try_finish("missing", work_left=_work_left, now_unix=_NOW)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.finished_unix is None


@pytest.mark.asyncio
async def test_an_update_can_require_the_chat_to_still_belong_to_the_writer() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x", "a1")])

    assert not await repository.update_target(
        campaign_id, "x", assigned_to="a2", expect_states=("queued",), state="writing"
    )
    assert (await _target(campaign_id, "x")).state == "queued"
    assert await repository.update_target(
        campaign_id, "x", assigned_to="a1", expect_states=("queued",), state="writing"
    )
    assert (await _target(campaign_id, "x")).state == "writing"


@pytest.mark.asyncio
async def test_a_keep_holds_only_on_the_status_it_was_decided_on() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    await repository.set_status(campaign_id, "running", run_id="r1", round=2)
    await repository.update_target(campaign_id, "x", state="skipped", skip_reason="deleted")
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None

    assert not await repository.keep_target(record.model_copy(update={"status": "stopped"}), "x")
    assert not await repository.keep_target(record.model_copy(update={"campaign_id": "no"}), "x")
    assert not (await _target(campaign_id, "x")).ignore_deleted

    assert await repository.keep_target(record, "x", state="queued", skip_reason=None)
    target = await _target(campaign_id, "x")
    assert (target.ignore_deleted, target.state) == (True, "queued")


@pytest.mark.asyncio
async def test_a_keep_and_a_finish_never_both_win() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    await repository.set_status(campaign_id, "running", run_id="r1", round=2)
    await repository.update_target(campaign_id, "x", state="skipped", skip_reason="deleted")
    seen = await repository.fetch_campaign(campaign_id)
    assert seen is not None

    # The finish got in first: the keep, decided on "running", finds it over.
    assert await repository.try_finish(campaign_id, work_left=_work_left, now_unix=_NOW)
    assert not await repository.keep_target(seen, "x", state="queued", skip_reason=None)

    target = await _target(campaign_id, "x")
    assert (target.state, target.ignore_deleted) == ("skipped", False)


@pytest.mark.asyncio
async def test_a_chat_kept_before_the_finish_keeps_the_run_going() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    await repository.set_status(campaign_id, "running", run_id="r1", round=2)
    await repository.update_target(campaign_id, "x", state="skipped", skip_reason="deleted")
    seen = await repository.fetch_campaign(campaign_id)
    assert seen is not None

    assert await repository.keep_target(seen, "x", state="queued", skip_reason=None)

    assert not await repository.try_finish(campaign_id, work_left=_work_left, now_unix=_NOW)
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "running"


@pytest.mark.asyncio
async def test_one_account_comes_back_from_a_halt() -> None:
    campaign_id = await _campaign()
    await repository.halt_account(campaign_id, "a1", "errors_in_a_row")
    await repository.halt_account(campaign_id, "a2", "errors_in_a_row")
    await repository.set_consecutive_errors(campaign_id, "a1", 5)

    await repository.revive_account(campaign_id, "a1")

    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].halted_reason, roster["a1"].consecutive_errors) == (
        "active",
        None,
        0,
    )
    assert roster["a2"].state == "halted"


@pytest.mark.asyncio
async def test_a_dead_workers_joins_start_over_but_the_others_go_on() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(
        campaign_id, [_seed("mine", "a1"), _seed("theirs", "a2"), _seed("rc", "a1")]
    )
    await repository.update_target(campaign_id, "mine", state="joining")
    await repository.update_target(campaign_id, "theirs", state="captcha")
    await repository.update_target(campaign_id, "rc", state="reconnecting", next_action_unix=1)

    await repository.settle_interrupted(campaign_id, account_id="a1")

    rows = {t.chat_key: t for t in await repository.list_targets(campaign_id)}
    assert (rows["mine"].state, rows["theirs"].state) == ("queued", "captcha")
    assert (rows["rc"].state, rows["rc"].next_action_unix) == ("reconnecting", 1)


@pytest.mark.asyncio
async def test_a_refusal_is_kept_on_the_chat_once_per_account() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x"), _seed("y")])
    assert (await _target(campaign_id, "x")).refused_by == []

    await repository.add_refusal(campaign_id, "x", "a1")
    await repository.add_refusal(campaign_id, "x", "a1")
    await repository.add_refusal(campaign_id, "x", "a2")
    await repository.add_refusal(campaign_id, "missing", "a2")

    assert (await _target(campaign_id, "x")).refused_by == ["a1", "a2"]
    assert (await _target(campaign_id, "y")).refused_by == []


@pytest.mark.asyncio
async def test_what_kind_of_chat_it_is_is_kept_for_the_deletion_check() -> None:
    campaign_id = await _campaign()
    await repository.replace_targets(campaign_id, [_seed("x")])
    assert (await _target(campaign_id, "x")).chat_kind is None

    await repository.update_target(campaign_id, "x", chat_kind="basic_group")

    assert (await _target(campaign_id, "x")).chat_kind == "basic_group"
