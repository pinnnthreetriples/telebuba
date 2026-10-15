"""A bulk message run and the other features never share one Telegram session.

The run claims its accounts in ``services._account_owner`` while its loop runs, so
warming, the listener, the comment engine and channel search see them as busy; it leaves
out an account another feature holds; and it gives every claim back however it ends.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.repositories import bulk_runs as repo
from schemas._warming_pool import PoolSearchCandidate
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.telegram_actions import ActionResult, SendChatMessage
from services import _account_owner, warming
from services.accounts import _bulk_claims, _bulk_runner, bulk_messages
from services.neurocomment import _gates
from services.neurocomment import _runtime as nc_runtime
from services.neurocomment import _runtime_operations as nc_operations
from services.warming import _exclusion, _pool_accounts
from tests.services.accounts.conftest import ALWAYS_OPEN
from tests.services.warming._pool_support import seed_account

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.bulk_runs import BulkRunRecord

    Probe = Callable[[str], Awaitable[None]]

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs", "bulk_clock")


class _Telegram:
    """Records every send; ``during`` runs inside the send, under the lifecycle lock."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.during: Probe | None = None

    async def execute(
        self,
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG002
    ) -> ActionResult:
        if self.during is not None:
            await self.during(account_id)
        self.sent.append((account_id, action.recipient))
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> _Telegram:
    fake = _Telegram()
    monkeypatch.setattr(bulk_messages, "execute", fake.execute)
    return fake


async def _start(**request: object) -> str:
    values: dict[str, object] = {
        "account_ids": ["a1"],
        "recipients": ["@rcpt1"],
        "text": "Hello",
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
        "schedule": ALWAYS_OPEN,
        **request,
    }
    job = await bulk_messages.start_bulk_message_job(
        BulkMessageRequest.model_validate(values), "owner-1"
    )
    return job.job_id


async def _finished(job_id: str) -> BulkMessageJob:
    job = await bulk_messages.get_bulk_message_job(job_id, "owner-1")
    assert job is not None
    return job


@pytest.mark.asyncio
async def test_an_account_another_feature_holds_sends_nothing(telegram: _Telegram) -> None:
    _account_owner.try_claim("held", "chat_broadcast", "campaign-1")
    job_id = await _start(account_ids=["held", "free"], recipients=["@rcpt1", "@rcpt2"])

    await bulk_messages.run_bulk_message_job(job_id)

    assert sorted(telegram.sent) == [("free", "rcpt1"), ("free", "rcpt2")]
    job = await _finished(job_id)
    held = [(r.recipient, r.status, r.error_code) for r in job.results if r.account_id == "held"]
    assert sorted(held) == [
        ("@rcpt1", "skipped", "account_busy"),
        ("@rcpt2", "skipped", "account_busy"),
    ]
    # The broadcast's claim is untouched, and nothing of the run's is left behind.
    assert _account_owner.owners() == {"held": "chat_broadcast"}


@pytest.mark.asyncio
async def test_a_warming_account_is_never_taken(
    telegram: _Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def warming_ids() -> list[str]:
        return ["warm"]

    monkeypatch.setattr(_bulk_claims, "list_warming_account_ids", warming_ids)
    job_id = await _start(account_ids=["warm"], recipients=["@rcpt1"])

    await bulk_messages.run_bulk_message_job(job_id)

    assert telegram.sent == []
    assert [(r.status, r.error_code) for r in (await _finished(job_id)).results] == [
        ("skipped", "account_busy")
    ]


@pytest.mark.asyncio
async def test_split_mode_hands_a_busy_accounts_recipients_to_the_free_one(
    telegram: _Telegram,
) -> None:
    _account_owner.try_claim("held", "masstagging", "campaign-1")
    job_id = await _start(
        account_ids=["held", "free"], recipients=["@rcpt1", "@rcpt2"], mode="split"
    )

    await bulk_messages.run_bulk_message_job(job_id)

    assert sorted(telegram.sent) == [("free", "rcpt1"), ("free", "rcpt2")]


@pytest.mark.asyncio
async def test_a_lent_account_is_left_out(telegram: _Telegram) -> None:
    assert _account_owner.lend("lent", "search-1")
    job_id = await _start(account_ids=["lent"], recipients=["@rcpt1"])

    await bulk_messages.run_bulk_message_job(job_id)

    assert telegram.sent == []
    assert _account_owner.owner_of("lent") is None


@pytest.mark.asyncio
async def test_the_sending_account_is_busy_to_every_other_feature(
    telegram: _Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_account("a1")

    async def no_warming() -> list[str]:
        return []

    monkeypatch.setattr(nc_runtime, "_list_warming_account_ids", no_warming)
    seen: list[object] = []

    async def look(account_id: str) -> None:
        seen.append(_account_owner.owner_of(account_id))
        seen.append(await _pool_accounts.search_standing(account_id))
        seen.append(await _pool_accounts.free_search_accounts())
        with pytest.raises(_exclusion.AccountUnavailableError) as refused:
            _exclusion.assert_not_campaign_held(account_id)
        seen.append(refused.value.code)
        with pytest.raises(nc_operations.ListenerBusyBulkMessagesError):
            await nc_operations._refuse_if_busy(account_id)
        seen.append(
            _gates._account_block_reason(
                account_id,
                "channel",
                1,
                datetime.now(UTC),
                None,  # ty: ignore[invalid-argument-type] - the ownership rung answers first
            )
        )
        assert _account_owner.try_claim(account_id, "neuroshilling", "campaign-1") == (
            "bulk_messages"
        )

    telegram.during = look
    job_id = await _start()

    await bulk_messages.run_bulk_message_job(job_id)

    owner, standing, free, code, reason = seen
    assert owner == "bulk_messages"
    assert isinstance(standing, PoolSearchCandidate)
    assert standing.busy_with == "bulk_messages"
    assert free == []
    assert code == "account_busy_bulk_messages"
    assert reason == "busy_bulk_messages"
    assert _account_owner.owner_of("a1") is None


@pytest.mark.asyncio
async def test_a_send_waits_for_a_pulled_search_to_give_the_account_back(
    telegram: _Telegram,
) -> None:
    searching = asyncio.Event()
    search_done = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    locked: list[bool] = []

    async def search() -> None:
        # What a consented pull does: the account's lifecycle lock for the whole search.
        async with warming.account_lock("a1"):
            searching.set()
            await search_done.wait()

    async def pull_after_first_send(account_id: str) -> None:
        # The send itself holds the lock, so the search can only start between sends.
        locked.append(warming.account_lock(account_id).locked())
        if not tasks:
            tasks.append(asyncio.create_task(search()))

    telegram.during = pull_after_first_send
    job_id = await _start(recipients=["@rcpt1", "@rcpt2"])
    run = asyncio.create_task(bulk_messages.run_bulk_message_job(job_id))
    async with asyncio.timeout(5):
        await searching.wait()
    # Real time, for the run's database writes: unlocked, the next send would be out.
    await asyncio.sleep(0.2)

    assert telegram.sent == [("a1", "rcpt1")]
    search_done.set()
    async with asyncio.timeout(5):
        await run
        await tasks[0]

    assert telegram.sent == [("a1", "rcpt1"), ("a1", "rcpt2")]
    assert locked == [True, True]


@pytest.mark.asyncio
async def test_the_claim_is_released_when_the_run_fails(
    telegram: _Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_usage(*_args: object) -> None:
        msg = "usage table gone"
        raise RuntimeError(msg)

    claimed: list[object] = []

    async def look(account_id: str) -> None:
        claimed.append(_account_owner.owner_of(account_id))

    telegram.during = look
    monkeypatch.setattr(_bulk_runner, "add_outreach_usage", broken_usage)
    job_id = await _start()

    await bulk_messages.run_bulk_message_job(job_id)

    assert claimed == ["bulk_messages"]
    assert (await _finished(job_id)).status == "cancelled"
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_the_claim_is_released_when_the_operator_cancels(telegram: _Telegram) -> None:
    sending = asyncio.Event()
    go_on = asyncio.Event()

    async def hold(_account_id: str) -> None:
        sending.set()
        await go_on.wait()

    telegram.during = hold
    job_id = await _start(recipients=["@rcpt1", "@rcpt2"])
    run = asyncio.create_task(bulk_messages.run_bulk_message_job(job_id))
    await sending.wait()
    assert _account_owner.owner_of("a1") == "bulk_messages"

    await bulk_messages.cancel_bulk_message_job(job_id, "owner-1")
    go_on.set()
    await run

    assert telegram.sent == [("a1", "rcpt1")]
    assert (await _finished(job_id)).status == "cancelled"
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_the_claim_is_released_when_shutdown_cancels_the_loop(telegram: _Telegram) -> None:
    sending = asyncio.Event()

    async def hang(_account_id: str) -> None:
        sending.set()
        await asyncio.Event().wait()

    telegram.during = hang
    job_id = await _start()
    bulk_messages.launch_bulk_message_job(job_id)
    await sending.wait()
    assert _account_owner.owner_of("a1") == "bulk_messages"

    await bulk_messages.shutdown_bulk_messages_on_shutdown()

    assert _account_owner.owners() == {}
    # The run stays live, so the next start resumes it.
    assert [run.id for run in await repo.list_live_bulk_runs()] == [job_id]


@pytest.mark.asyncio
async def test_a_second_loop_over_the_same_run_does_nothing(
    telegram: _Telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    sending = asyncio.Event()
    go_on = asyncio.Event()

    async def hold(_account_id: str) -> None:
        sending.set()
        await go_on.wait()

    telegram.during = hold
    job_id = await _start()
    first = asyncio.create_task(bulk_messages.run_bulk_message_job(job_id))
    await sending.wait()
    loads: list[str] = []
    load = bulk_messages._load_run

    async def counting_load(record: BulkRunRecord) -> _bulk_runner.BulkRun:
        loads.append(record.id)
        return await load(record)

    monkeypatch.setattr(bulk_messages, "_load_run", counting_load)

    await bulk_messages.run_bulk_message_job(job_id)

    # The duplicate did not even load the run, neither sent nor dropped the claim.
    assert loads == []
    assert _account_owner.owner_of("a1") == "bulk_messages"
    go_on.set()
    await first
    assert telegram.sent == [("a1", "rcpt1")]
    assert _account_owner.owners() == {}


@pytest.mark.asyncio
async def test_two_loops_started_together_send_once(telegram: _Telegram) -> None:
    job_id = await _start()

    # Both pass the first check before either has loaded the run.
    await asyncio.gather(
        bulk_messages.run_bulk_message_job(job_id),
        bulk_messages.run_bulk_message_job(job_id),
    )

    assert telegram.sent == [("a1", "rcpt1")]
    assert _account_owner.owners() == {}
