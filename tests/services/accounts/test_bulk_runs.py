"""Persistent bulk runs: hours, client zones, breaks, start time, concurrency, restart."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.repositories import bulk_runs as repo
from schemas.bulk_messages import BulkMessageJob, BulkMessageRequest
from schemas.run_schedule import RunSchedule
from schemas.telegram_actions import ActionResult, SendChatMessage
from services.accounts import _bulk_runner, bulk_messages
from services.accounts._bulk_plan import next_open, zone_for
from tests.services.accounts.conftest import ALWAYS_OPEN

if TYPE_CHECKING:
    from schemas.bulk_runs import BulkRunItem
    from tests.services.accounts.conftest import FakeClock

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

# Monday 2026-10-12 07:00 UTC = 10:00 in Moscow, 02:00 in Bogotá.
_BOGOTA_PHONE = "+573001234567"


def _schedule(**overrides: object) -> dict[str, object]:
    return {**ALWAYS_OPEN, **overrides}


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    **request: object,
) -> tuple[BulkMessageJob, list[tuple[datetime, str, str]]]:
    calls: list[tuple[datetime, str, str]] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append((clock.now, account_id, action.recipient))
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    values: dict[str, object] = {
        "account_ids": ["a1"],
        "text": "Hello",
        "min_delay_seconds": 0,
        "max_delay_seconds": 0,
        **request,
    }
    job = await bulk_messages.start_bulk_message_job(
        BulkMessageRequest.model_validate(values), "owner-1"
    )
    await bulk_messages.run_bulk_message_job(job.job_id)
    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    return finished, calls


def test_the_window_opens_at_the_next_working_moment() -> None:
    schedule = RunSchedule()  # 09:00-20:00 Moscow, Monday to Saturday
    monday_late = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # 21:00 in Moscow
    assert next_open(monday_late, "Europe/Moscow", schedule) == datetime(
        2026, 10, 13, 6, 0, tzinfo=UTC
    )
    inside = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    assert next_open(inside, "Europe/Moscow", schedule) == inside
    saturday_late = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)
    # Sunday is off: the next window is Monday morning.
    assert next_open(saturday_late, "Europe/Moscow", schedule) == datetime(
        2026, 10, 19, 6, 0, tzinfo=UTC
    )


def test_the_clients_zone_is_used_only_when_the_phone_pins_one() -> None:
    schedule = RunSchedule()
    assert zone_for(_BOGOTA_PHONE, schedule) == ("America/Bogota", "client")
    # Russia spans many zones: no silent guess, the operator's clock and a mark.
    assert zone_for("+79161234567", schedule) == ("Europe/Moscow", "operator")
    assert zone_for(None, schedule) == ("Europe/Moscow", "operator")
    assert zone_for(_BOGOTA_PHONE, RunSchedule(client_timezone=False)) == (
        "Europe/Moscow",
        "operator",
    )


def test_a_schedule_rejects_impossible_windows() -> None:
    with pytest.raises(ValueError, match="after work_start"):
        RunSchedule(work_start="20:00", work_end="09:00")
    with pytest.raises(ValueError, match="HH:MM"):
        RunSchedule(work_start="9am")
    with pytest.raises(ValueError, match="operator_timezone"):
        RunSchedule(operator_timezone="Mars/Olympus")
    with pytest.raises(ValueError, match="Monday"):
        RunSchedule(work_days=[7])


@pytest.mark.asyncio
async def test_a_run_outside_working_hours_waits_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    bulk_clock.now = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # 21:00 Moscow
    reasons: list[str | None] = []
    sleep = _bulk_runner._sleep

    async def watching_sleep(wakeup: asyncio.Event, seconds: float | None) -> None:
        reasons.extend(run.wait_reason for run in await repo.list_live_bulk_runs())
        await sleep(wakeup, seconds)

    monkeypatch.setattr(_bulk_runner, "_sleep", watching_sleep)
    job, calls = await _run(
        monkeypatch,
        bulk_clock,
        recipients=["@rcpt1"],
        schedule=_schedule(work_start="09:00", work_end="20:00", work_days=[0, 1, 2, 3, 4, 5]),
    )

    assert [moment for moment, _a, _r in calls] == [datetime(2026, 10, 13, 6, 0, tzinfo=UTC)]
    assert "hours" in reasons
    assert (job.wait_reason, job.next_send_at) == (None, None)


@pytest.mark.asyncio
async def test_each_recipient_waits_for_its_own_morning(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    job, calls = await _run(
        monkeypatch,
        bulk_clock,
        recipients=["@bogota_client", "@typed_by_hand"],
        recipient_phones={"@bogota_client": _BOGOTA_PHONE},
        schedule=_schedule(work_start="09:00", work_end="20:00"),
    )

    # 10:00 in Moscow: the hand-typed one goes now, Bogotá waits for its own 09:00.
    assert calls == [
        (datetime(2026, 10, 12, 7, 0, tzinfo=UTC), "a1", "typed_by_hand"),
        (datetime(2026, 10, 12, 14, 0, tzinfo=UTC), "a1", "bogota_client"),
    ]
    assert {r.recipient: r.time_basis for r in job.results} == {
        "@typed_by_hand": "operator",
        "@bogota_client": "client",
    }


@pytest.mark.asyncio
async def test_an_account_rests_after_a_series(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    _job, calls = await _run(
        monkeypatch,
        bulk_clock,
        recipients=["@rcpt1", "@rcpt2", "@rcpt3"],
        schedule=_schedule(
            series_size=2, series_break_min_seconds=900, series_break_max_seconds=900
        ),
    )

    start = datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    assert [moment for moment, _a, _r in calls] == [start, start, start + timedelta(seconds=900)]


@pytest.mark.asyncio
async def test_a_scheduled_run_starts_at_its_time(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,
) -> None:
    start_at = datetime(2026, 10, 12, 9, 30, tzinfo=UTC)

    _job, calls = await _run(
        monkeypatch,
        bulk_clock,
        recipients=["@rcpt1"],
        start_at=start_at.isoformat(),
        schedule=ALWAYS_OPEN,
    )

    assert [moment for moment, _a, _r in calls] == [start_at]


@pytest.mark.asyncio
@pytest.mark.parametrize(("concurrency", "expected_peak"), [(1, 1), (2, 2)])
async def test_no_more_accounts_send_at_once_than_allowed(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,  # noqa: ARG001
    concurrency: int,
    expected_peak: int,
) -> None:
    in_flight = 0
    peak = 0

    async def slow_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", slow_execute)
    request = BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1", "a2", "a3"],
            "recipients": ["@rcpt1", "@rcpt2", "@rcpt3", "@rcpt4"],
            "text": "Hello",
            "mode": "split",
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "schedule": _schedule(concurrency=concurrency),
        }
    )
    job = await bulk_messages.start_bulk_message_job(request, "owner-1")
    await bulk_messages.run_bulk_message_job(job.job_id)

    assert peak == expected_peak


@pytest.mark.asyncio
async def test_a_restart_resumes_the_run_and_never_resends_a_send_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,  # noqa: ARG001
) -> None:
    calls: list[str] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append(action.recipient)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    request = BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1"],
            "recipients": ["@rcpt1", "@rcpt2"],
            "text": "Hello",
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "schedule": ALWAYS_OPEN,
        }
    )
    job = await bulk_messages.start_bulk_message_job(request, "owner-1")
    # The process died while the first send was in flight.
    [first, _second] = await repo.list_bulk_run_items(job.job_id)
    await repo.save_bulk_run_item(first.model_copy(update={"status": "sending"}))

    launched: list[asyncio.Task[None]] = []
    launch = bulk_messages.launch_bulk_message_job

    def tracking_launch(job_id: str) -> None:
        launch(job_id)
        launched.append(bulk_messages._TASKS[job_id])

    monkeypatch.setattr(bulk_messages, "launch_bulk_message_job", tracking_launch)
    await bulk_messages.reconcile_bulk_messages_on_startup()
    await asyncio.gather(*launched)

    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert calls == ["rcpt2"]
    assert [(r.recipient, r.status) for r in finished.results] == [
        ("@rcpt1", "unconfirmed"),
        ("@rcpt2", "ok"),
    ]
    assert finished.status == "completed"


@pytest.mark.asyncio
async def test_shutdown_leaves_the_run_live_for_the_next_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold = asyncio.Event()
    stopped: list[str] = []

    async def held_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        try:
            await hold.wait()
        except asyncio.CancelledError:
            stopped.append(action.recipient)
            raise
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", held_execute)
    request = BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1"],
            "recipients": ["@rcpt1"],
            "text": "Hello",
            "schedule": ALWAYS_OPEN,
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
        }
    )
    job = await bulk_messages.start_bulk_message_job(request, "owner-1")
    bulk_messages.launch_bulk_message_job(job.job_id)
    await asyncio.sleep(0.05)

    await bulk_messages.shutdown_bulk_messages_on_shutdown()

    record = await repo.get_bulk_run(job.job_id)
    assert record is not None
    assert record.status == "running"
    assert bulk_messages._TASKS == {}
    # The send in flight was stopped too, not left running on its own.
    assert stopped == ["rcpt1"]


@pytest.mark.asyncio
async def test_cancel_ends_a_long_wait_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1"],
            "recipients": ["@rcpt1"],
            "text": "Hello",
            "start_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "schedule": ALWAYS_OPEN,
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
        }
    )
    monkeypatch.setattr(bulk_messages, "execute", None)
    job = await bulk_messages.start_bulk_message_job(request, "owner-1")
    bulk_messages.launch_bulk_message_job(job.job_id)
    # Held from the start: a finished task leaves the registry at once.
    task = bulk_messages._TASKS[job.job_id]
    await asyncio.sleep(0.05)

    await bulk_messages.cancel_bulk_message_job(job.job_id, "owner-1")
    await asyncio.wait_for(task, timeout=5)

    finished = await bulk_messages.get_bulk_message_job(job.job_id, "owner-1")
    assert finished is not None
    assert (finished.status, finished.completed) == ("cancelled", 0)


def _one_recipient() -> BulkMessageRequest:
    return BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1"],
            "recipients": ["@rcpt1"],
            "text": "Hello",
            "schedule": ALWAYS_OPEN,
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
        }
    )


@pytest.mark.asyncio
async def test_a_second_live_run_is_refused_even_past_the_services_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await bulk_messages.start_bulk_message_job(_one_recipient(), "owner-1")

    async def nothing_live() -> list[object]:
        return []

    # Two starts racing: both pass the check before either has written its run.
    monkeypatch.setattr(repo, "list_live_bulk_runs", nothing_live)
    with pytest.raises(ValueError, match="bulk_message_run_active"):
        await bulk_messages.start_bulk_message_job(_one_recipient(), "owner-1")


@pytest.mark.asyncio
async def test_a_send_whose_result_cannot_be_stored_ends_the_run_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,  # noqa: ARG001
) -> None:
    calls: list[str] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        calls.append(action.recipient)
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    job = await bulk_messages.start_bulk_message_job(_one_recipient(), "owner-1")
    save = repo.save_bulk_run_item
    saves = 0

    async def flaky_save(item: BulkRunItem) -> int:
        nonlocal saves
        saves += 1
        if saves == 2:  # the result after the send
            msg = "database is locked"
            raise RuntimeError(msg)
        return await save(item)

    monkeypatch.setattr(repo, "save_bulk_run_item", flaky_save)
    await bulk_messages.run_bulk_message_job(job.job_id)

    record = await repo.get_bulk_run(job.job_id)
    assert record is not None
    assert record.status == "cancelled"
    [item] = await repo.list_bulk_run_items(job.job_id)
    assert item.status == "unconfirmed"
    assert calls == ["rcpt1"]


@pytest.mark.asyncio
async def test_a_send_that_could_not_be_claimed_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
    bulk_clock: FakeClock,  # noqa: ARG001
) -> None:
    monkeypatch.setattr(bulk_messages, "execute", None)
    job = await bulk_messages.start_bulk_message_job(_one_recipient(), "owner-1")

    async def broken_save(_item: object) -> int:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(repo, "save_bulk_run_item", broken_save)
    await bulk_messages.run_bulk_message_job(job.job_id)

    record = await repo.get_bulk_run(job.job_id)
    assert record is not None
    assert record.status == "cancelled"
    [item] = await repo.list_bulk_run_items(job.job_id)
    assert item.status == "pending"


@pytest.mark.asyncio
async def test_a_failed_send_stops_the_other_sends_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hold = asyncio.Event()
    stopped: list[str] = []

    async def fake_execute(
        account_id: str,
        action: SendChatMessage,
        *,
        domain: str,  # noqa: ARG001
    ) -> ActionResult:
        if action.recipient == "slow":
            try:
                await hold.wait()
            except asyncio.CancelledError:
                stopped.append(action.recipient)
                raise
        return ActionResult(status="ok", action_type=action.action_type, account_id=account_id)

    monkeypatch.setattr(bulk_messages, "execute", fake_execute)
    request = BulkMessageRequest.model_validate(
        {
            "account_ids": ["a1", "a2"],
            "recipients": ["@slow", "@fast"],
            "text": "Hello",
            "mode": "split",
            "min_delay_seconds": 0,
            "max_delay_seconds": 0,
            "schedule": _schedule(concurrency=2),
        }
    )
    job = await bulk_messages.start_bulk_message_job(request, "owner-1")
    finish = _bulk_runner.BulkRun._finish

    async def failing_finish(
        self: _bulk_runner.BulkRun, item: BulkRunItem, **fields: object
    ) -> None:
        if item.recipient == "@fast":
            msg = "database is locked"
            raise RuntimeError(msg)
        await finish(self, item, **fields)

    monkeypatch.setattr(_bulk_runner.BulkRun, "_finish", failing_finish)
    await asyncio.wait_for(bulk_messages.run_bulk_message_job(job.job_id), timeout=5)

    # The slow send was stopped with the run, not left to write after it settled.
    assert stopped == ["slow"]
    items = {i.recipient: i.status for i in await repo.list_bulk_run_items(job.job_id)}
    assert items == {"@slow": "unconfirmed", "@fast": "unconfirmed"}
