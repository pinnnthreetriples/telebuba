"""The lead queue's service: adding numbers, removing one, settings, pause, and run kinds."""

from __future__ import annotations

import pytest

from core.repositories import bulk_runs as runs_repo
from core.repositories import lead_queue as repo
from schemas.bulk_messages import BulkMessageRequest
from schemas.lead_queue import LeadInput, LeadQueueAddRequest, LeadQueueSettingsUpdate
from services.accounts import bulk_messages, lead_queue

pytestmark = pytest.mark.usefixtures("empty_bulk_jobs")


def _add(*entries: tuple[str, str | None]) -> LeadQueueAddRequest:
    return LeadQueueAddRequest(leads=[LeadInput(phone=p, name=n) for p, n in entries])


def _request(recipient: str = "@client") -> BulkMessageRequest:
    return BulkMessageRequest(account_ids=["a1"], recipients=[recipient], text="Hi")


@pytest.mark.asyncio
async def test_numbers_are_normalised_and_queued_once() -> None:
    first = await lead_queue.add_leads(
        _add(("+7 (999) 123-45-67", " Иван "), ("79991234567", None), ("call me", None))
    )
    second = await lead_queue.add_leads(_add(("+79991234567", "Пётр"), ("+15550001111", "")))

    assert (first.added, first.duplicates, first.invalid) == (1, 1, 1)
    assert (second.added, second.duplicates, second.invalid) == (1, 1, 0)
    leads = await repo.list_leads()
    assert [(lead.phone, lead.client_name, lead.status) for lead in leads] == [
        ("+79991234567", "Иван", "pending"),
        ("+15550001111", None, "pending"),
    ]


@pytest.mark.asyncio
async def test_only_a_lead_no_run_holds_can_be_removed() -> None:
    await lead_queue.add_leads(_add(("+15550001111", None), ("+15550002222", None)))
    waiting, sending = await repo.list_leads()
    sending.status = "messaging"
    await repo.save_leads([sending])

    assert waiting.id is not None
    assert sending.id is not None
    assert await lead_queue.remove_lead(waiting.id) == "removed"
    assert await lead_queue.remove_lead(sending.id) == "busy"
    assert await lead_queue.remove_lead(999) == "not_found"
    assert [lead.phone for lead in await repo.list_leads()] == ["+15550002222"]


def test_a_running_queue_needs_accounts_and_a_message() -> None:
    with pytest.raises(ValueError, match="accounts and a message"):
        LeadQueueSettingsUpdate(account_ids=["a1"], text="  ", paused=False)
    with pytest.raises(ValueError, match="accounts and a message"):
        LeadQueueSettingsUpdate(account_ids=[], text="Hi", paused=False)
    assert LeadQueueSettingsUpdate(paused=True).account_ids == []


@pytest.mark.asyncio
async def test_an_unsaved_queue_is_paused_and_a_save_records_its_owner() -> None:
    view = await lead_queue.get_lead_queue(10)
    assert view.settings.paused is True

    saved = await lead_queue.update_lead_queue_settings(
        LeadQueueSettingsUpdate(account_ids=["a1", "a2"], text="Hi", paused=False), "owner-1"
    )

    assert (saved.account_ids, saved.text, saved.paused, saved.owner_user_id) == (
        ["a1", "a2"],
        "Hi",
        False,
        "owner-1",
    )


@pytest.mark.asyncio
async def test_pausing_stops_the_queues_send_run_and_leaves_the_operators_alone() -> None:
    manual = await bulk_messages.start_bulk_message_job(_request("@one"), "owner-1")
    queue = await bulk_messages.start_bulk_message_job(
        _request("@two"), "owner-1", kind="lead_queue", run_id="queue-run"
    )

    await lead_queue.update_lead_queue_settings(LeadQueueSettingsUpdate(paused=True), "owner-1")

    queue_row = await runs_repo.get_bulk_run(queue.job_id)
    manual_row = await runs_repo.get_bulk_run(manual.job_id)
    assert queue_row is not None
    assert manual_row is not None
    assert (queue_row.id, queue_row.cancel_requested) == ("queue-run", True)
    assert manual_row.cancel_requested is False


@pytest.mark.asyncio
async def test_the_queues_run_never_blocks_or_shows_in_the_send_window() -> None:
    await bulk_messages.start_bulk_message_job(
        _request("@two"), "owner-1", kind="lead_queue", run_id="queue-run"
    )

    assert await bulk_messages.get_active_bulk_message_job("owner-1") is None
    assert await bulk_messages.get_latest_bulk_message_job("owner-1") is None
    manual = await bulk_messages.start_bulk_message_job(_request("@one"), "owner-1")
    active = await bulk_messages.get_active_bulk_message_job("owner-1")
    assert active is not None
    assert active.job_id == manual.job_id
    with pytest.raises(ValueError, match="bulk_message_run_active"):
        await bulk_messages.start_bulk_message_job(_request("@three"), "owner-1", kind="lead_queue")


@pytest.mark.asyncio
async def test_the_view_shows_counts_and_the_newest_leads_first() -> None:
    await lead_queue.add_leads(_add(("+15550001111", None), ("+15550002222", None)))
    first, _ = await repo.list_leads()
    first.status = "not_found"
    await repo.save_leads([first])

    view = await lead_queue.get_lead_queue(1)

    assert view.counts == {"pending": 1, "not_found": 1}
    assert [lead.phone for lead in view.items] == ["+15550002222"]
