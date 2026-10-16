"""The lead queue's guards: pause races, removed leads, dead accounts, re-checks, orphans."""

from __future__ import annotations

import pytest

from core.repositories import bulk_runs as runs_repo
from core.repositories import lead_queue as repo
from schemas.bulk_messages import BulkMessageRequest
from schemas.lead_queue import LeadQueueSettings, LeadQueueSettingsUpdate
from services.accounts import _lead_queue_lookup, bulk_messages, lead_queue
from services.accounts._lead_queue_runner import LeadQueueRunner
from tests.services.accounts.test_lead_queue_runner import (
    _by_phone,
    _now,
    _phones,
    _setup,
    _Telegram,
)

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")

_PAUSED = LeadQueueSettings(account_ids=["a1"], text="Hello", paused=True, owner_user_id="owner-1")


async def _queue_run(run_id: str = "queue-run") -> None:
    await bulk_messages.start_bulk_message_job(
        BulkMessageRequest(account_ids=["a1"], recipients=["501"], text="Hi"),
        "owner-1",
        kind="lead_queue",
        run_id=run_id,
    )


@pytest.mark.asyncio
async def test_a_paused_queue_cancels_a_run_that_slipped_past_the_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(monkeypatch, _Telegram())
    await _queue_run()
    await repo.save_lead_queue_settings(_PAUSED)

    await LeadQueueRunner().tick(_now())

    record = await runs_repo.get_bulk_run("queue-run")
    assert record is not None
    assert record.cancel_requested is True


@pytest.mark.asyncio
async def test_a_pause_saved_while_the_run_is_stored_cancels_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(1)
    launched = await _setup(monkeypatch, _Telegram({phones[0]: 501}), phones=phones)
    start = bulk_messages.start_bulk_message_job

    async def start_then_pause(*args: object, **kwargs: object) -> object:
        job = await start(*args, **kwargs)  # ty: ignore[invalid-argument-type]
        await repo.save_lead_queue_settings(_PAUSED)
        return job

    monkeypatch.setattr(bulk_messages, "start_bulk_message_job", start_then_pause)

    await LeadQueueRunner().tick(_now())

    record = await runs_repo.get_bulk_run(launched[0])
    assert record is not None
    # Still launched: its loop sees the cancel at once and settles the row.
    assert record.cancel_requested is True


@pytest.mark.asyncio
async def test_a_removed_lead_is_never_claimed_for_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    await _setup(monkeypatch, _Telegram(), phones=_phones(2))
    kept, removed = await repo.list_leads()
    for lead in (kept, removed):
        lead.status, lead.account_id, lead.user_id = "found", "a1", lead.id
    await repo.save_leads([kept, removed])
    assert removed.id is not None
    assert kept.id is not None
    assert await repo.delete_lead(removed.id, {"found"})

    claimed = await repo.claim_leads([kept.id, removed.id], "run-1")

    assert claimed == {kept.id}
    assert [(lead.status, lead.run_id) for lead in await repo.list_leads()] == [
        ("messaging", "run-1")
    ]


@pytest.mark.asyncio
async def test_a_dead_accounts_lead_fails_instead_of_waiting_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(1)
    launched = await _setup(monkeypatch, _Telegram({phones[0]: 501}), phones=phones)
    runner = LeadQueueRunner()
    await runner.tick(_now())
    item = (await runs_repo.list_bulk_run_items(launched[0]))[0]
    item.status, item.error_code = "skipped", "session_dead"
    await runs_repo.save_bulk_run_item(item)
    await runs_repo.update_bulk_run(launched[0], status="completed")

    await runner.sync(_now())

    lead = (await _by_phone())[phones[0]]
    assert (lead.status, lead.error_code) == ("failed", "session_dead")
    assert "a1" not in runner.send_rest


@pytest.mark.asyncio
async def test_rechecks_no_account_can_take_never_starve_fresh_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(2)
    telegram = _Telegram()
    await _setup(monkeypatch, telegram, accounts=("a1",), phones=phones)
    stuck = (await _by_phone())[phones[0]]
    stuck.recheck_account_id = "a1"
    await repo.save_leads([stuck])
    monkeypatch.setattr(_lead_queue_lookup, "_PENDING_WINDOW", 1)

    await LeadQueueRunner().tick(_now())

    assert telegram.batches == [("a1", [phones[1]])]


@pytest.mark.asyncio
async def test_a_live_run_nobody_drives_is_launched_again(monkeypatch: pytest.MonkeyPatch) -> None:
    launched = await _setup(monkeypatch, _Telegram())
    await _queue_run("orphan")

    await LeadQueueRunner().tick(_now())

    assert launched == ["orphan"]


@pytest.mark.asyncio
async def test_a_pause_request_holds_until_a_resume_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _Telegram()
    await _setup(monkeypatch, telegram, accounts=("a1",), phones=_phones(1))
    runner = LeadQueueRunner()
    monkeypatch.setattr(lead_queue._LOOP, "runner", runner)
    # A pause saved after this tick read the (unpaused) settings.
    runner.cancel.set()

    await runner.tick(_now())
    assert telegram.batches == []

    await lead_queue.update_lead_queue_settings(
        LeadQueueSettingsUpdate(account_ids=["a1"], text="Hello", paused=False), "owner-1"
    )
    await runner.tick(_now())
    assert telegram.batches == [("a1", _phones(1))]


@pytest.mark.asyncio
async def test_a_client_the_history_rules_refuse_is_skipped_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phones = _phones(1)
    launched = await _setup(monkeypatch, _Telegram({phones[0]: 501}), phones=phones)
    runner = LeadQueueRunner()
    await runner.tick(_now())
    item = (await runs_repo.list_bulk_run_items(launched[0]))[0]
    item.status, item.error_code = "skipped", "recently_messaged"
    await runs_repo.save_bulk_run_item(item)
    await runs_repo.update_bulk_run(launched[0], status="completed")

    await runner.sync(_now())

    lead = (await _by_phone())[phones[0]]
    assert (lead.status, lead.error_code) == ("skipped", "recently_messaged")


@pytest.mark.asyncio
async def test_a_pause_signals_the_loop_before_it_cancels_the_live_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup(monkeypatch, _Telegram())
    await _queue_run()
    runner = LeadQueueRunner()
    monkeypatch.setattr(lead_queue._LOOP, "runner", runner)
    seen: list[bool] = []
    cancel = bulk_messages.cancel_bulk_message_job

    async def watching_cancel(job_id: str, owner_user_id: str) -> object:
        seen.append(runner.cancel.is_set())
        return await cancel(job_id, owner_user_id)

    monkeypatch.setattr(bulk_messages, "cancel_bulk_message_job", watching_cancel)

    await lead_queue.update_lead_queue_settings(LeadQueueSettingsUpdate(paused=True), "owner-1")

    assert seen == [True]
