"""The lead queue: numbers the operator adds at any time, worked through automatically.

Each lead is looked up within the working window and, when found, gets the queue's
message from the account that found it, through a ``lead_queue`` bulk run. One
queue serves the app; its settings name the accounts and the message, and pausing
it also stops its live send run. State lives in the database, and the loop resumes
from it after a restart (see ``_lead_queue_runner``).
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from core.repositories import bulk_runs as runs_repo
from core.repositories import lead_queue as repo
from schemas.lead_queue import (
    LeadQueueAddResult,
    LeadQueueItem,
    LeadQueueSettings,
    LeadQueueView,
)
from services.accounts import bulk_messages
from services.accounts._lead_queue_runner import LeadQueueRunner
from services.accounts.contact_lookup import normalize_phone

if TYPE_CHECKING:
    from schemas.lead_queue import LeadQueueAddRequest, LeadQueueSettingsUpdate

# Not yet handed to a send run: removing the lead cannot cut a send short.
_REMOVABLE = frozenset({"pending", "found"})


@dataclass
class _Loop:
    runner: LeadQueueRunner | None = None
    task: asyncio.Task[None] | None = None
    # Serialises settings saves, so an older pause never lands after a newer resume.
    settings_lock: asyncio.Lock | None = None


# This process's queue loop, so a change wakes it and shutdown can stop it.
_LOOP = _Loop()


def _wake(*, paused: bool | None = None) -> None:
    """Wake the loop; a settings save also sets (pause) or clears (resume) its cancel."""
    if _LOOP.runner is not None:
        if paused is True:
            _LOOP.runner.cancel.set()
        elif paused is False:
            _LOOP.runner.cancel.clear()
        _LOOP.runner.wakeup.set()


async def add_leads(data: LeadQueueAddRequest) -> LeadQueueAddResult:
    """Queue every valid number not queued before; a number lives in the queue once."""
    now = datetime.now(UTC).isoformat()
    leads: dict[str, LeadQueueItem] = {}
    invalid = 0
    for entry in data.leads:
        phone = normalize_phone(entry.phone)
        if phone is None:
            invalid += 1
            continue
        key = phone.removeprefix("+")
        name = (entry.name or "").strip() or None
        leads.setdefault(
            key, LeadQueueItem(phone=phone, phone_key=key, client_name=name, created_at=now)
        )
    added = await repo.add_leads(list(leads.values()))
    _wake()
    return LeadQueueAddResult(
        added=added, duplicates=len(data.leads) - invalid - added, invalid=invalid
    )


async def get_lead_queue(limit: int) -> LeadQueueView:
    live = await runs_repo.list_live_bulk_runs("lead_queue")
    return LeadQueueView(
        settings=await repo.load_lead_queue_settings(),
        counts=await repo.count_leads_by_status(),
        items=await repo.list_leads(limit=limit, newest_first=True),
        wait_reason=live[0].wait_reason if live else None,
        next_send_at=live[0].next_send_at if live else None,
    )


async def remove_lead(lead_id: int) -> Literal["removed", "not_found", "busy"]:
    """Remove a lead that no send run holds yet."""
    if await repo.delete_lead(lead_id, _REMOVABLE):
        return "removed"
    return "busy" if await repo.get_lead(lead_id) is not None else "not_found"


async def update_lead_queue_settings(
    data: LeadQueueSettingsUpdate, owner_user_id: str
) -> LeadQueueSettings:
    """Save the queue's accounts, message and pause; a pause also stops the live send run.

    A run already sending keeps the message it started with; the next one uses the new.
    The loop is signalled right after the save, before the run is cancelled, so a lookup
    in flight stops at once.
    """
    async with _LOOP.settings_lock or nullcontext():
        saved = await repo.save_lead_queue_settings(
            LeadQueueSettings(**data.model_dump(), owner_user_id=owner_user_id)
        )
        _wake(paused=saved.paused)
        if saved.paused:
            for record in await runs_repo.list_live_bulk_runs("lead_queue"):
                await bulk_messages.cancel_bulk_message_job(record.id, record.owner_user_id)
    return saved


async def start_lead_queue_on_startup() -> None:
    """Start the queue's loop; its live send run resumes with the other bulk runs."""
    _LOOP.runner = LeadQueueRunner()
    _LOOP.settings_lock = asyncio.Lock()
    _LOOP.task = asyncio.create_task(_LOOP.runner.run_forever())


async def shutdown_lead_queue_on_shutdown() -> None:
    """Stop the loop; a lookup in flight is cancelled and its leads stay pending."""
    task, _LOOP.task, _LOOP.runner, _LOOP.settings_lock = _LOOP.task, None, None, None
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
