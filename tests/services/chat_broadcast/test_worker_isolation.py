"""One task's fault stays its own: the others go on, the account is counted and tried again."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.repositories import chat_broadcast as repository
from core.repositories.logs import list_recent_logs
from services import _account_owner
from services.chat_broadcast import _coordinator, _recovery, _rotation, _send, _worker
from tests.services.chat_broadcast.fakes import refused, run_to_end, seed
from tests.services.chat_broadcast.layout import chat, setup

if TYPE_CHECKING:
    from schemas.chat_broadcast_records import TargetRecord
    from schemas.logs import LogEntry
    from services.chat_broadcast._context import RunContext
    from tests.services.chat_broadcast.fakes import Clock, FakeTelegram

_PAUSE = 60
_CHATS = ("@alpha", "@beta", "@gamma", "@delta")
_BOOM = "boom: a proxy, a path or a token would be here"


def _crashes_of(rows: list[LogEntry]) -> list[LogEntry]:
    return [row for row in rows if row.event == "chat_broadcast_worker_crashed"]


async def _crash_logs() -> list[LogEntry]:
    return _crashes_of(await list_recent_logs(limit=200))


def _crashing_handle(monkeypatch: pytest.MonkeyPatch, *, account: str, times: int) -> list[str]:
    """Make ``account``'s worker blow up ``times`` times on a chat; the chats it hit."""
    real = _worker.handle
    hit: list[str] = []

    async def handle(ctx: RunContext, account_id: str, target: TargetRecord) -> _send.Step:
        if account_id == account and len(hit) < times:
            hit.append(target.chat_key)
            raise RuntimeError(_BOOM)
        return await real(ctx, account_id, target)

    monkeypatch.setattr(_worker, "handle", handle)
    return hit


@pytest.mark.asyncio
async def test_a_crash_in_one_workers_task_leaves_the_run_and_the_other_account_going(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1", "a2"), targets=_CHATS, messages=("Hi",))
    hit = _crashing_handle(monkeypatch, account="a1", times=1)

    await run_to_end(campaign_id)

    assert len(hit) == 1
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    assert {t.state for t in await repository.list_targets(campaign_id)} == {"done"}
    assert len(telegram.sent) == len(_CHATS)
    [crash] = await _crash_logs()
    # Only the class of the error is logged: its text can carry anything.
    assert crash.account_id == "a1"
    assert crash.extra == {"campaign_id": campaign_id, "error_type": "RuntimeError"}
    assert _BOOM not in repr(crash)


@pytest.mark.asyncio
async def test_the_crashed_account_starts_again_after_a_pause_and_finishes_its_chats(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=_CHATS[:2], messages=("Hi",))
    _crashing_handle(monkeypatch, account="a1", times=1)
    started = clock.now()

    await run_to_end(campaign_id)

    assert clock.now() - started >= _PAUSE
    assert [s.account_id for s in telegram.sent] == ["a1", "a1"]
    [account] = await repository.list_roster(campaign_id)
    # The error it was counted for is cleared by the first send that went out.
    assert (account.state, account.consecutive_errors) == ("active", 0)


@pytest.mark.asyncio
async def test_a_series_of_crashes_takes_the_account_out_and_its_chats_go_on(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tried again later is the recovery tests' business: here the halt is what is read.
    monkeypatch.setattr(settings.chat_broadcast, "halt_retry_max", 0)
    campaign_id = await seed(
        accounts=("a1", "a2"), targets=_CHATS, messages=("Hi",), max_consecutive_errors=2
    )
    _crashing_handle(monkeypatch, account="a1", times=10)

    await run_to_end(campaign_id)

    roster = {r.account_id: r for r in await repository.list_roster(campaign_id)}
    assert (roster["a1"].state, roster["a1"].halted_reason) == ("halted", "worker_crash")
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
    # Nothing it held was lost: the other account played every chat.
    assert {s.account_id for s in telegram.sent} == {"a2"}
    assert len(telegram.sent) == len(_CHATS)
    assert len(await _crash_logs()) == 2


@pytest.mark.asyncio
async def test_a_crash_in_a_resting_accounts_waker_is_counted_and_the_wait_resumes(
    telegram: FakeTelegram, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(accounts=("a1",), targets=_CHATS[:1], messages=("one", "two"))
    telegram.send_answers["a1"] = [refused("flood_wait", flood_wait_seconds=3000)]
    real = _rotation.sit_out
    calls: list[str] = []

    async def sit_out(ctx: RunContext, account_id: str) -> bool:
        calls.append(account_id)
        if len(calls) == 1:
            raise RuntimeError(_BOOM)
        return await real(ctx, account_id)

    monkeypatch.setattr(_rotation, "sit_out", sit_out)
    started = clock.now()

    await run_to_end(campaign_id)

    assert telegram.texts() == ["one", "two"]
    assert len(calls) == 2
    assert clock.now() - started >= 3000
    assert len(await _crash_logs()) == 1


@pytest.mark.asyncio
async def test_a_crash_in_a_helper_task_is_logged_and_the_helper_starts_again(
    telegram: FakeTelegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a9"),
        targets=_CHATS[:3],
        messages=("Hi",),
        between_chats={"min": 120, "max": 120},
    )
    # Held by another feature: the run keeps a claimer waiting for it.
    assert _account_owner.try_claim("a9", "warming", "w-1") is None
    real = _recovery.watch_left_out
    calls: list[int] = []

    async def watch(ctx: RunContext) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(_BOOM)
        return await real(ctx)

    monkeypatch.setattr(_recovery, "watch_left_out", watch)

    await run_to_end(campaign_id)

    assert len(telegram.sent) == 3
    assert len(calls) == 2
    [crash] = await _crash_logs()
    assert (crash.account_id, crash.extra["error_type"]) == (None, "RuntimeError")


@pytest.mark.asyncio
async def test_with_skip_errors_off_a_failed_chat_still_stops_the_run(
    telegram: FakeTelegram,
) -> None:
    campaign_id = await seed(
        accounts=("a1", "a2"), targets=_CHATS[:2], messages=("Hi",), skip_errors=False
    )
    telegram.send_answers["a1"] = [refused(error_type="RPCError")]
    telegram.send_answers["a2"] = [refused(error_type="RPCError")]

    await run_to_end(campaign_id)

    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    # The operator's choice is not a fault to absorb: nobody restarts, the run is failed.
    assert (record.status, record.last_error) == ("failed", "RPCError")
    assert await _crash_logs() == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_ensure_starts_one_worker_per_live_account_and_never_a_second() -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a1", "a1")])
    tasks = _coordinator._Tasks(ctx)
    try:
        await tasks.ensure()
        first = dict(tasks.workers)
        await tasks.ensure()

        assert set(first) == {"a1", "a2"}
        assert tasks.workers == first
        assert tasks.wakers == {}
        assert tasks.helpers == {}
    finally:
        await tasks.cancel()
    assert campaign_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("telegram")
async def test_no_worker_is_started_when_nothing_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    campaign_id, ctx = await setup(("a1", "a2"), [chat("chat0", "a1", "a1", "done")])
    started: list[str] = []

    async def work(_ctx: RunContext, account_id: str) -> str:
        started.append(account_id)
        return "finished"

    monkeypatch.setattr(_worker, "work", work)

    outcome = await _coordinator.play(ctx)

    assert outcome == _coordinator.RunOutcome("done")
    assert started == []
    record = await repository.fetch_campaign(campaign_id)
    assert record is not None
    assert record.status == "done"
