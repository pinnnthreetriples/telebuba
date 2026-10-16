"""The lead queue's loop: paced lookups, daily caps, silent limits, send runs and read-back."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories import bulk_runs as runs_repo
from core.repositories import lead_queue as repo
from core.repositories.outreach import add_outreach_usage, save_outreach_limits
from schemas.bulk_messages import BulkMessageRequest
from schemas.lead_queue import LeadInput, LeadQueueAddRequest, LeadQueueSettings
from schemas.outreach_limits import OutreachDailyUsage, OutreachLimits, OutreachLimitsUpdate
from schemas.run_schedule import RunSchedule
from schemas.telegram_actions import ActionResult, SendChatMessage
from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch
from services.accounts import _lead_queue_runner, bulk_messages, contact_lookup, lead_queue
from services.accounts._bulk_plan import next_open
from services.accounts._lead_queue_runner import LeadQueueRunner, lookup_spacing
from tests.services.accounts.conftest import ALWAYS_OPEN

if TYPE_CHECKING:
    from schemas.lead_queue import LeadQueueItem
    from schemas.telegram_actions_contacts import LookupContactsByPhone
    from tests.services.accounts.conftest import FakeClock

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")


class _Telegram:
    """Records each ImportContacts batch; ``users`` maps a phone to the user it finds."""

    def __init__(self, users: dict[str, int] | None = None) -> None:
        self.users = users or {}
        self.batches: list[tuple[str, list[str]]] = []

    async def __call__(
        self, account_id: str, action: LookupContactsByPhone
    ) -> ContactLookupBatchResult:
        self.batches.append((account_id, list(action.phones)))
        found = [p for p in action.phones if p in self.users]
        return ContactLookupBatchResult(
            matches=[ContactLookupMatch(phone=p, user_id=self.users[p]) for p in found],
            unresolved=[p for p in action.phones if p not in found],
        )


def _now() -> datetime:
    # The lookup counts usage on the real UTC day, so the queue's clock must agree.
    return datetime.now(UTC)


def _phones(count: int) -> list[str]:
    return [f"+1555000{index:04d}" for index in range(1, count + 1)]


async def _setup(
    monkeypatch: pytest.MonkeyPatch,
    telegram: _Telegram,
    *,
    accounts: tuple[str, ...] = ("a1", "a2"),
    phones: list[str] | None = None,
    schedule: dict[str, object] | None = None,
    **limits: object,
) -> list[str]:
    monkeypatch.setattr(contact_lookup, "execute_read", telegram)
    await save_outreach_limits(
        OutreachLimitsUpdate.model_validate(
            {
                "lookup_min_delay_seconds": 0,
                "lookup_max_delay_seconds": 0,
                "bulk_min_delay_seconds": 0,
                "bulk_max_delay_seconds": 0,
                "schedule": schedule or ALWAYS_OPEN,
                **limits,
            }
        )
    )
    await repo.save_lead_queue_settings(
        LeadQueueSettings(
            account_ids=list(accounts), text="Hello", paused=False, owner_user_id="owner-1"
        )
    )
    launched: list[str] = []
    monkeypatch.setattr(bulk_messages, "launch_bulk_message_job", launched.append)
    if phones:
        await lead_queue.add_leads(LeadQueueAddRequest(leads=[LeadInput(phone=p) for p in phones]))
    return launched


async def _by_phone() -> dict[str, LeadQueueItem]:
    return {lead.phone: lead for lead in await repo.list_leads()}


async def _send_all(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, ActionResult]) -> None:
    """Drive every live queue run to its end; each recipient answers its outcome."""

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        return outcomes.get(action.recipient) or ActionResult(
            status="ok", action_type=action.action_type, account_id=account_id
        )

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    for record in await runs_repo.list_live_bulk_runs("lead_queue"):
        await bulk_messages.run_bulk_message_job(record.id)


def test_lookups_spread_one_accounts_allowance_over_the_window() -> None:
    limits = OutreachLimits(contact_imports_per_day=15, lookup_batch_size=3)
    # 09:00-20:00 is 11 hours; 15 numbers in batches of 3 are 5 passes.
    assert lookup_spacing(limits) == timedelta(hours=11) / 5
    tight = OutreachLimits(
        contact_imports_per_day=200, lookup_batch_size=1, lookup_max_delay_seconds=600
    )
    assert lookup_spacing(tight) == timedelta(seconds=600)


@pytest.mark.asyncio
async def test_a_pass_gives_each_account_one_batch_and_waits_a_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _Telegram()
    await _setup(monkeypatch, telegram, phones=_phones(10))
    runner = LeadQueueRunner()
    now = _now()

    wake_at = await runner.tick(now)

    assert telegram.batches == [("a1", _phones(10)[0:6:2]), ("a2", _phones(10)[1:6:2])]
    open_limits = OutreachLimits(schedule=RunSchedule.model_validate(ALWAYS_OPEN))
    assert wake_at == now + lookup_spacing(open_limits)
    statuses = [lead.status for lead in await repo.list_leads()]
    assert statuses == ["not_found"] * 6 + ["pending"] * 4
    # Before the spacing is over, a change wakes the loop but checks nothing.
    await runner.tick(now + timedelta(minutes=1))
    assert len(telegram.batches) == 2


@pytest.mark.asyncio
async def test_a_lead_that_does_not_fit_todays_cap_waits_for_the_next_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _Telegram()
    await _setup(monkeypatch, telegram, accounts=("a1",), phones=_phones(3))
    now = _now()
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day=now.date().isoformat(), contact_imports=14)
    )
    runner = LeadQueueRunner()

    await runner.tick(now)
    runner.next_lookup_at = None
    wake_at = await runner.tick(now)

    assert telegram.batches == [("a1", [_phones(3)[0]])]
    tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    assert wake_at == tomorrow
    assert [lead.status for lead in await repo.list_leads()] == ["not_found", "pending", "pending"]


@pytest.mark.asyncio
async def test_nothing_is_looked_up_outside_the_window_or_while_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _Telegram()
    # Open only on a weekday other than today's.
    closed_day = (_now().weekday() + 3) % 7
    await _setup(
        monkeypatch,
        telegram,
        phones=_phones(2),
        schedule={**ALWAYS_OPEN, "work_days": [closed_day]},
    )
    now = _now()

    wake_at = await LeadQueueRunner().tick(now)
    schedule = RunSchedule.model_validate({**ALWAYS_OPEN, "work_days": [closed_day]})
    assert wake_at is not None
    assert wake_at == next_open(now, schedule.operator_timezone, schedule)
    assert wake_at > now

    await repo.save_lead_queue_settings(LeadQueueSettings(account_ids=["a1"], text="Hi"))
    assert await LeadQueueRunner().tick(now) is None
    assert telegram.batches == []


@pytest.mark.asyncio
async def test_a_found_lead_is_sent_by_the_account_that_found_it(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,  # noqa: ARG001 - the run's clock
) -> None:
    phones = _phones(4)
    telegram = _Telegram({phones[1]: 501, phones[2]: 502})
    launched = await _setup(monkeypatch, telegram, phones=phones)
    runner = LeadQueueRunner()

    await runner.tick(_now())

    leads = await _by_phone()
    assert (leads[phones[1]].status, leads[phones[1]].account_id) == ("messaging", "a2")
    assert (leads[phones[2]].status, leads[phones[2]].account_id) == ("messaging", "a1")
    assert len(launched) == 1
    record = await runs_repo.get_bulk_run(launched[0])
    assert record is not None
    assert (record.kind, record.owner_user_id) == ("lead_queue", "owner-1")
    request = BulkMessageRequest.model_validate_json(record.request_json)
    assert request.mode == "split"
    assert request.recipient_accounts == {"501": "a2", "502": "a1"}
    assert request.recipient_phones == {"501": phones[1], "502": phones[2]}

    await _send_all(monkeypatch, {})
    await runner.tick(_now())

    leads = await _by_phone()
    assert [leads[p].status for p in phones] == ["not_found", "sent", "sent", "not_found"]


@pytest.mark.asyncio
async def test_a_send_that_failed_or_never_went_out_is_read_back_onto_its_lead(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,  # noqa: ARG001 - the run's clock
) -> None:
    phones = _phones(3)
    telegram = _Telegram({phones[0]: 501, phones[1]: 502, phones[2]: 503})
    await _setup(monkeypatch, telegram, accounts=("a1",), phones=phones)
    runner = LeadQueueRunner()
    await runner.tick(_now())

    refused = ActionResult(
        status="failed",
        action_type="send_chat_message",
        account_id="a1",
        error_type="UserPrivacyRestricted",
    )
    flood = ActionResult(status="peer_flood", action_type="send_chat_message", account_id="a1")
    await _send_all(monkeypatch, {"501": refused, "502": flood})
    now = _now()
    await runner.tick(now)

    leads = await _by_phone()
    assert leads[phones[0]].status == "failed"
    assert leads[phones[0]].error_code is not None
    # A peer flood provably sent nothing, and the next one was never tried: both wait
    # for the account to rest, then go out in a later run.
    assert (leads[phones[1]].status, leads[phones[1]].run_id) == ("found", None)
    assert leads[phones[2]].status == "found"
    assert runner.send_rest["a1"] > now
    assert await runs_repo.list_live_bulk_runs("lead_queue") == []


@pytest.mark.asyncio
async def test_a_cancelled_or_lost_run_puts_its_unsent_leads_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(3)
    telegram = _Telegram({phones[0]: 501, phones[1]: 502, phones[2]: 503})
    launched = await _setup(monkeypatch, telegram, accounts=("a1",), phones=phones)
    runner = LeadQueueRunner()
    await runner.tick(_now())
    run_id = launched[0]
    items = await runs_repo.list_bulk_run_items(run_id)
    items[0].status = "sending"
    await runs_repo.save_bulk_run_item(items[0])
    await runs_repo.update_bulk_run(run_id, status="cancelled")
    # A lead marked for a run that was never stored (a crash between the two writes).
    lost = (await _by_phone())[phones[2]]
    lost.run_id = "never-stored"
    await repo.save_leads([lost])

    await runner.sync(_now())

    leads = await _by_phone()
    assert leads[phones[0]].status == "unconfirmed"
    assert (leads[phones[1]].status, leads[phones[1]].run_id) == ("found", None)
    assert (leads[phones[2]].status, leads[phones[2]].run_id) == ("found", None)


@pytest.mark.asyncio
async def test_an_account_held_elsewhere_hands_its_leads_back_for_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(1)
    await _setup(monkeypatch, _Telegram({phones[0]: 501}), accounts=("a1",), phones=phones)
    runner = LeadQueueRunner()
    await runner.tick(_now())
    run_id = (await repo.list_leads())[0].run_id
    assert run_id is not None
    item = (await runs_repo.list_bulk_run_items(run_id))[0]
    item.status, item.error_code = "skipped", "account_busy"
    await runs_repo.save_bulk_run_item(item)
    await runs_repo.update_bulk_run(run_id, status="completed")
    now = _now()

    await runner.sync(now)
    wake_at = await runner.tick(now)

    assert (await repo.list_leads())[0].status == "found"
    assert runner.send_rest["a1"] == now + timedelta(minutes=30)
    assert wake_at == now + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_a_silent_limit_across_passes_sends_the_streak_to_another_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(3)
    telegram = _Telegram()
    await _setup(
        monkeypatch,
        telegram,
        accounts=("a1",),
        phones=phones[:2],
        lookup_batch_size=1,
        silent_limit_streak=2,
    )
    now = _now()
    # a1 found people on an earlier day, so a long miss streak is suspicious.
    await add_outreach_usage(
        OutreachDailyUsage(account_id="a1", day="2026-01-01", contact_matches=1)
    )
    runner = LeadQueueRunner()

    await runner.tick(now)
    runner.next_lookup_at = None
    await runner.tick(now)

    leads = await _by_phone()
    assert [(leads[p].status, leads[p].recheck_account_id) for p in phones[:2]] == [
        ("pending", "a1"),
        ("pending", "a1"),
    ]
    assert runner.lookup_rest == {"a1": now.date().isoformat()}

    # Another account re-checks them; a1 is never handed a re-check of its own miss.
    await repo.save_lead_queue_settings(
        LeadQueueSettings(account_ids=["a2"], text="Hi", paused=False, owner_user_id="owner-1")
    )
    runner.next_lookup_at = None
    await runner.tick(now)
    assert telegram.batches[-1] == ("a2", [phones[0]])


@pytest.mark.asyncio
async def test_the_same_client_under_a_second_number_is_messaged_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(2)
    await _setup(monkeypatch, _Telegram({phones[0]: 501, phones[1]: 501}), phones=phones)

    await LeadQueueRunner().tick(_now())

    leads = await _by_phone()
    assert leads[phones[0]].status == "messaging"
    assert (leads[phones[1]].status, leads[phones[1]].error_code) == (
        "skipped",
        "duplicate_client",
    )


@pytest.mark.asyncio
async def test_a_failing_tick_never_ends_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = LeadQueueRunner()
    ticks: list[int] = []
    sleeps: list[float | None] = []

    async def broken_tick(_now: datetime) -> datetime | None:
        ticks.append(1)
        raise RuntimeError

    async def fake_sleep(_wakeup: asyncio.Event, seconds: float | None) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(runner, "tick", broken_tick)
    monkeypatch.setattr(_lead_queue_runner, "_sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await runner.run_forever()

    assert len(ticks) == 2
    assert all(s is not None and s > 0 for s in sleeps)


@pytest.mark.asyncio
async def test_startup_runs_the_loop_and_shutdown_stops_it() -> None:
    await lead_queue.start_lead_queue_on_startup()
    task = lead_queue._LOOP.task
    assert task is not None
    await asyncio.sleep(0)

    await lead_queue.shutdown_lead_queue_on_shutdown()

    assert task.done()
    assert lead_queue._LOOP.runner is None
