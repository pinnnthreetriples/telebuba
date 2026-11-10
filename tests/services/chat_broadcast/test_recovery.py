"""Self-recovery: the run waits for accounts that can come back, and says why when none can."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from core.repositories.logs import list_recent_logs
from schemas.chat_broadcast_records import JournalKey, TargetSeed
from services import _account_owner
from services import chat_broadcast as service
from services.chat_broadcast import _moves, _recovery, _runtime, _seams, _send, _state
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.logs import LogEntry
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_RETRY = 1800
_RPC_FAILURE = "RPCError"


def _on_sleep(monkeypatch: pytest.MonkeyPatch, hook: Callable[[], Awaitable[None]]) -> None:
    """Run ``hook`` after every sleep of the engine: lets a test act while the run waits."""
    sleep = _seams.sleep

    async def hooked(seconds: float) -> None:
        await sleep(seconds)
        await hook()

    monkeypatch.setattr(_seams, "sleep", hooked)


async def _events(name: str) -> list[LogEntry]:
    return [row for row in await list_recent_logs(limit=500) if row.event == name]


async def _came_back(reason: str) -> list[str | None]:
    rows = await _events("chat_broadcast_account_back")
    return [row.account_id for row in rows if row.extra.get("reason") == reason]


@pytest.mark.asyncio
async def test_an_account_another_feature_frees_midway_is_taken_and_given_chats(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a9"),
        targets=("@chat1", "@chat2", "@chat3", "@chat4"),
        messages=("Hi",),
        loop=True,
        rounds=0,
        stop_mode="time",
        stop_hours=3,
        account_limit=False,
        rest_minutes={"min": 10, "max": 10},
        between_chats={"min": 30, "max": 30},
    )
    assert _account_owner.try_claim("a9", "warming", "w-1") is None
    freed_at = clock.now() + 1800
    held = True

    async def free_it() -> None:
        nonlocal held
        if held and clock.now() >= freed_at:
            held = False
            _account_owner.release("a9", "warming", "w-1")

    _on_sleep(monkeypatch, free_it)

    await run_to_end(campaign_id)

    assert not held
    # a9 was left out at the start, took work only once free, and then wrote.
    assert await _came_back("freed") == ["a9"]
    assert "a9" in {s.account_id for s in telegram.sent}
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"


@pytest.mark.asyncio
async def test_a_restart_with_every_account_busy_keeps_the_run_and_its_chats_waiting(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a2",), targets=(), messages=("Hi",))
    await repository.set_status(campaign_id, "running", run_id="run-2", round=1)
    await repository.replace_targets(
        campaign_id,
        [
            TargetSeed(
                chat_key="alpha",
                raw="@alpha",
                kind="public",
                username="alpha",
                peer_id=77,
                assigned_account_id="a2",
                member_account_id="a2",
            )
        ],
    )
    assert _account_owner.try_claim("a2", "warming", "w-1") is None
    waiting: list[str] = []
    freed = False

    async def free_when_waiting() -> None:
        nonlocal freed
        chats = await repository.list_targets(campaign_id)
        record = await repository.fetch_campaign(campaign_id)
        if not freed and chats and all(t.state == "waiting_account" for t in chats):
            assert record is not None
            waiting.append(record.status)
            freed = True
            _account_owner.release("a2", "warming", "w-1")

    _on_sleep(monkeypatch, free_when_waiting)

    await service.reconcile_chat_broadcast_on_startup()
    await _runtime._TASKS[campaign_id]

    # Not stalled: the run stood by, its chats waiting for an account, and went on when one came.
    assert waiting == ["running"]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("done", None)
    assert [s.account_id for s in telegram.sent] == ["a2"]


@pytest.mark.asyncio
async def test_busy_accounts_alone_do_not_stall_a_run_whose_others_were_blocked(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1", "a9"), targets=("@alpha",), messages=("Hi",))
    assert _account_owner.try_claim("a9", "warming", "w-1") is None
    telegram.send_answers["a1"] = [refused("peer_flood")]
    seen: list[str] = []

    async def free_after_the_block() -> None:
        roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
        if roster["a1"].state == "halted" and not seen:
            record = await repository.fetch_campaign(campaign_id)
            assert record is not None
            seen.append(record.status)
            _account_owner.release("a9", "warming", "w-1")

    _on_sleep(monkeypatch, free_after_the_block)

    await run_to_end(campaign_id)

    # a1 was halted for a spam block; a9 might still be freed, so the run did not stall.
    assert seen == ["running"]
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert [s.account_id for s in telegram.sent] == ["a9"]


@pytest.mark.asyncio
async def test_an_account_stopped_by_errors_is_tried_again_after_the_pause_and_a_send_clears_it(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"),
        targets=("@chat1", "@chat2", "@chat3", "@chat4"),
        messages=("Hi",),
        loop=True,
        rounds=0,
        stop_mode="time",
        stop_hours=3,
        account_limit=False,
        max_consecutive_errors=2,
        rest_minutes={"min": 5, "max": 5},
        between_chats={"min": 30, "max": 30},
    )
    telegram.send_answers["a1"] = [refused(error_type=_RPC_FAILURE)] * 2
    cleared: list[tuple[str, str | None]] = []
    real = _state.reset_retries

    def reset(campaign: str, account: str | None = None) -> None:
        cleared.append((campaign, account))
        real(campaign, account)

    monkeypatch.setattr(_state, "reset_retries", reset)
    started = clock.now()

    await run_to_end(campaign_id)

    halted = [r for r in await _events("chat_broadcast_account_halted") if r.account_id == "a1"]
    assert [r.extra["reason"] for r in halted] == ["errors_in_a_row"]
    assert await _came_back("retry") == ["a1"]
    assert clock.now() - started >= _RETRY
    # It wrote again once back, and that send started its count of tries over.
    assert "a1" in {s.account_id for s in telegram.sent}
    assert (campaign_id, "a1") in cleared
    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].halted_reason) == ("active", None)


@pytest.mark.asyncio
async def test_an_account_that_keeps_failing_is_brought_back_a_limited_number_of_times(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1",),
        targets=tuple(f"@chat{index}" for index in range(10)),
        messages=("Hi",),
        max_consecutive_errors=2,
    )
    telegram.send_answers["a1"] = [refused(error_type=_RPC_FAILURE)] * 100

    await run_to_end(campaign_id)

    # Halted, back three times, halted again: then nothing is left to wait for.
    assert await _came_back("retry") == ["a1"] * settings.chat_broadcast.halt_retry_max
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("stalled", "accounts_errors")
    [roster] = await repository.list_roster(campaign_id)
    assert (roster.state, roster.halted_reason) == ("halted", "errors_in_a_row")
    # Its chats wait for an account instead of looking assigned to a dead one.
    waiting = [
        t for t in await repository.list_targets(campaign_id) if t.state == "waiting_account"
    ]
    assert waiting


@pytest.mark.asyncio
async def test_a_dead_session_ends_in_a_stall_that_says_so_and_is_never_tried_again(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=("@alpha",), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("failed", error_message="account_frozen")]

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("stalled", "accounts_dead")
    assert await _came_back("retry") == []
    assert telegram.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "retried"),
    [
        ("errors_in_a_row", True),
        ("worker_crash", True),
        ("spam_block", False),
        ("account_deactivated", False),
        ("account_frozen", False),
        ("session_dead", False),
        ("ChannelsTooMuchError", False),
        ("a_reason_nobody_knows", False),
    ],
)
async def test_only_errors_and_crashes_are_retried(reason: str, *, retried: bool) -> None:
    campaign_id, _ctx = await setup(("a1",), [chat("chat0", "a1", "a1")])
    await repository.halt_account(campaign_id, "a1", reason)

    candidates = await _recovery.retryable(campaign_id)

    assert [item.account_id for item in candidates] == (["a1"] if retried else [])


@pytest.mark.asyncio
async def test_a_stall_names_a_spam_block_before_errors_before_a_dead_session() -> None:
    campaign_id, _ctx = await setup(("a1", "a2", "a3"), [chat("chat0", "a1", "a1")])
    assert await _recovery.stall_cause(campaign_id) == "accounts_dead"
    await repository.halt_account(campaign_id, "a3", "session_dead")
    assert await _recovery.stall_cause(campaign_id) == "accounts_dead"
    await repository.halt_account(campaign_id, "a2", "worker_crash")
    assert await _recovery.stall_cause(campaign_id) == "accounts_errors"
    await repository.halt_account(campaign_id, "a1", "spam_block")
    assert await _recovery.stall_cause(campaign_id) == "accounts_spam"


@pytest.mark.asyncio
async def test_without_any_halted_account_a_stall_is_put_down_to_dead_accounts() -> None:
    campaign_id = await seed(accounts=(), targets=("@alpha",))

    assert await _recovery.stall_cause(campaign_id) == "accounts_dead"


@pytest.mark.asyncio
async def test_start_gives_the_retry_count_back_and_wakes_the_accounts_that_can_come() -> None:
    campaign_id, _ctx = await setup(("a1", "a2"), [chat("chat0", "a1", "a1")])
    await repository.halt_account(campaign_id, "a1", "errors_in_a_row")
    await repository.halt_account(campaign_id, "a2", "session_dead")
    for _attempt in range(3):
        _state.note_retry(campaign_id, "a1")
    assert await _recovery.retryable(campaign_id) == []

    await _runtime._revive(campaign_id)

    assert _state.retries(campaign_id, "a1") == 0
    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a2"].state) == ("active", "halted")
    assert await _came_back("restart") == ["a1"]


@pytest.mark.asyncio
async def test_a_halt_is_noted_with_its_time(clock: Clock) -> None:
    campaign_id, ctx = await setup(("a1",), [chat("chat0", "a1", "a1")])

    await _moves.halt_account(ctx, "a1", "errors_in_a_row")

    assert _state.halted_at(campaign_id, "a1") == clock.now()


@pytest.mark.asyncio
async def test_a_halt_this_process_never_saw_counts_from_the_first_look(clock: Clock) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a2", "a2")])
    await repository.halt_account(campaign_id, "a1", "errors_in_a_row")
    assert _state.halted_at(campaign_id, "a1") is None
    started = clock.now()

    assert await _recovery.watch_halted(ctx)

    assert clock.now() - started >= _RETRY
    assert _state.retries(campaign_id, "a1") == 1
    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].consecutive_errors) == ("active", 0)
    assert await _came_back("retry") == ["a1"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_the_helpers_end_by_themselves_when_there_is_nothing_to_wait_for() -> None:
    campaign_id, ctx = await setup(("a1",), [chat("chat0", "a1", "a1")])

    # No account is held by anybody else, none is halted: both come back at once, with
    # nothing done — they would spin on the engine's clock forever if they waited.
    assert await asyncio.wait_for(_recovery.watch_left_out(ctx), timeout=5) is False
    assert await asyncio.wait_for(_recovery.watch_halted(ctx), timeout=5) is False
    assert campaign_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_the_helpers_give_up_waiting_when_the_volume_is_reached() -> None:
    campaign_id, ctx = await setup(("a1", "a2", "a9"), [chat("chat0", "a1", "a1")], stop_messages=1)
    # a9 is taken by another feature, a2 halted for errors: both would be waited for.
    _account_owner.release("a9", "chat_broadcast", campaign_id)
    assert _account_owner.try_claim("a9", "warming", "w-1") is None
    await repository.halt_account(campaign_id, "a2", "errors_in_a_row")
    # One message already went out in this run: that is the whole volume.
    key = JournalKey(run_id="run-1", chat_key="chat0", round=1, step_index=0)
    await repository.claim_message(key, campaign_id=campaign_id, account_id="a1", kind="text")
    await repository.settle_message(key, "sent", text="Hi")
    assert await _send.volume_reached(ctx)

    assert await asyncio.wait_for(_recovery.watch_halted(ctx), timeout=5) is False
    assert await asyncio.wait_for(_recovery.watch_left_out(ctx), timeout=5) is False


@pytest.mark.asyncio
async def test_a_claim_stuck_on_another_features_lock_holds_up_neither_workers_nor_ticks(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.warming import account_lock  # noqa: PLC0415 - avoids an import cycle

    campaign_id = await seed(
        accounts=("a1", "a9"),
        targets=("@alpha", "@beta"),
        messages=("Hi",),
        between_chats={"min": 120, "max": 120},
    )
    assert _account_owner.try_claim("a9", "warming", "w-1") is None
    release = asyncio.Event()
    holders: list[asyncio.Task[None]] = []

    async def hold_the_lock() -> None:
        async with account_lock("a9"):
            await release.wait()

    async def free_it_and_lock_it() -> None:
        # Warming lets go of a9 and, a moment later, holds its lifecycle lock for minutes.
        if not holders:
            _account_owner.release("a9", "warming", "w-1")
            holders.append(asyncio.create_task(hold_the_lock()))

    _on_sleep(monkeypatch, free_it_and_lock_it)
    try:
        await run_to_end(campaign_id)
    finally:
        release.set()
        await asyncio.gather(*holders)

    # The claimer waited for that lock in a task of its own: a1 played every chat meanwhile.
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert {s.account_id for s in telegram.sent} == {"a1"}
    assert len(telegram.sent) == 2


async def _a_dead_a1_and_a_held_a9(telegram: FakeTelegram) -> str:
    campaign_id = await seed(accounts=("a1", "a9"), targets=("@alpha",), messages=("Hi",))
    telegram.send_answers["a1"] = [refused("failed", error_message="account_frozen")]
    # Held by another feature for good: nothing this run does can take it.
    assert _account_owner.try_claim("a9", "warming", "w-1") is None
    return campaign_id


@pytest.mark.asyncio
async def test_a_run_that_waits_only_for_accounts_it_can_never_take_stalls_and_says_so(
    telegram: FakeTelegram, clock: Clock
) -> None:
    campaign_id = await _a_dead_a1_and_a_held_a9(telegram)
    started = clock.now()

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("stalled", "accounts_busy")
    # It waited the whole time it was told to before it gave up.
    assert clock.now() - started >= settings.chat_broadcast.claim_give_up_seconds
    [alpha] = await repository.list_targets(campaign_id)
    assert alpha.state == "waiting_account"
    assert telegram.sent == []


@pytest.mark.asyncio
async def test_an_account_freed_in_time_is_taken_and_the_wait_never_stalls_the_run(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await _a_dead_a1_and_a_held_a9(telegram)
    freed_at = clock.now() + settings.chat_broadcast.claim_give_up_seconds // 2
    freed = False

    async def free_a9() -> None:
        nonlocal freed
        if not freed and clock.now() >= freed_at:
            freed = True
            _account_owner.release("a9", "warming", "w-1")

    _on_sleep(monkeypatch, free_a9)

    await run_to_end(campaign_id)

    assert freed
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert (record.status, record.last_error) == ("done", None)
    assert [s.account_id for s in telegram.sent] == ["a9"]


@pytest.mark.asyncio
async def test_the_wait_for_held_accounts_starts_over_when_one_is_taken_or_work_resumes() -> None:
    assert _state.stuck_since("c1", 100) == 100
    assert _state.stuck_since("c1", 500) == 100
    _state.clear_stuck("c1")
    assert _state.stuck_since("c1", 700) == 700
    _state.forget_campaign("c1")
    assert _state.stuck_since("c1", 900) == 900


@pytest.mark.asyncio
async def test_a_join_request_pending_approval_is_not_rewritten_when_nobody_is_live() -> None:
    chats = [
        chat("pending", "a1", None, "pending_approval"),
        chat("joining", "a1", None, "joining"),
        chat("writing", "a1", "a1", "writing"),
        chat("queued", "a1", None),
        chat("done", "a1", "a1", "done"),
    ]
    campaign_id, ctx = await setup(("a1",), chats)

    await _recovery.mark_waiting_account(ctx)

    states = {t.chat_key: t.state for t in await repository.list_targets(campaign_id)}
    # The request belongs to the account that sent it: a second one would be sent otherwise.
    assert states == {
        "pending": "pending_approval",
        "joining": "joining",
        "writing": "waiting_account",
        "queued": "waiting_account",
        "done": "done",
    }
