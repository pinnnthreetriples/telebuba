"""The loop that works the lead queue: look leads up, hand found ones to a send run.

Each tick it first reads back what the queue's send run did, then — inside the
operator's working window, when the queue is configured and not paused — runs one
lookup pass (``_lead_queue_lookup``) once the previous one is a spacing apart, and
starts a ``lead_queue`` bulk run for the found leads when none is live. The run is
the persistent bulk-run machinery: working hours in the client's zone, daily caps,
pauses, series breaks and concurrency are all its own, and each lead is pinned to
the account that found it, the only one that can address a user without @username.

Every lead state lives in ``lead_queue_items``; the loop's own memory (spacing and
resting accounts) is only pacing, so a restart loses nothing but a rest.
"""

from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from core.repositories import bulk_runs as runs_repo
from core.repositories import lead_queue as repo
from core.repositories.outreach import load_outreach_limits
from schemas.bulk_messages import BulkMessageRequest
from schemas.run_schedule import clock_minutes
from services.accounts import bulk_messages
from services.accounts._bulk_plan import next_open, next_utc_day
from services.accounts._bulk_runner import STOP_CODES
from services.accounts._lead_queue_lookup import run_lookup_pass

if TYPE_CHECKING:
    from schemas.bulk_runs import BulkRunItem, BulkRunRecord
    from schemas.lead_queue import LeadQueueItem, LeadQueueSettings
    from schemas.outreach_limits import OutreachLimits

# How often a live send run is read back into the leads.
SYNC_SECONDS = 60
# After a tick failed: try again, but never in a tight loop.
_RETRY_SECONDS = 300
_MAX_RUN_LEADS = 500
_MAX_RUN_ACCOUNTS = 50
logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Seam: the queue's clock."""
    return datetime.now(UTC)


async def _sleep(wakeup: asyncio.Event, seconds: float | None) -> None:
    """Seam: wait ``seconds`` (forever when ``None``) or until ``wakeup`` fires."""
    with suppress(TimeoutError):
        await asyncio.wait_for(wakeup.wait(), timeout=seconds)


def lookup_spacing(limits: OutreachLimits) -> timedelta:
    """Time between two lookup passes: one account's daily allowance spread over the window."""
    schedule = limits.schedule
    window = (clock_minutes(schedule.work_end) - clock_minutes(schedule.work_start)) * 60
    passes = math.ceil(limits.contact_imports_per_day / limits.lookup_batch_size)
    return timedelta(seconds=max(window / passes, limits.lookup_max_delay_seconds))


def _ready(configured: LeadQueueSettings) -> bool:
    return not configured.paused and bool(configured.account_ids) and bool(configured.text.strip())


class LeadQueueRunner:
    def __init__(self) -> None:
        self.wakeup = asyncio.Event()
        # The queue never stops on its own; this only interrupts a lookup pass.
        self.cancel = asyncio.Event()
        self.next_lookup_at: datetime | None = None
        # Account -> UTC day it rests from lookups (stopped or silently limited).
        self.lookup_rest: dict[str, str] = {}
        # Account -> when it may send again after a send run stopped it.
        self.send_rest: dict[str, datetime] = {}

    async def run_forever(self) -> None:
        while True:
            self.wakeup.clear()
            try:
                wake_at = await self.tick(_now())
            except Exception as exc:  # noqa: BLE001 - one bad tick must not end the queue
                logger.warning("lead queue tick failed: %s", type(exc).__name__)
                wake_at = _now() + timedelta(seconds=_RETRY_SECONDS)
            delay = None if wake_at is None else max((wake_at - _now()).total_seconds(), 0)
            await _sleep(self.wakeup, delay)

    async def tick(self, now: datetime) -> datetime | None:
        """One pass over the queue; returns when the next one is due (``None``: on a change)."""
        await self.sync(now)
        configured = await repo.load_lead_queue_settings()
        live = await runs_repo.list_live_bulk_runs("lead_queue")
        wakes: list[datetime] = [now + timedelta(seconds=SYNC_SECONDS)] if live else []
        if not _ready(configured):
            return min(wakes, default=None)
        limits = await load_outreach_limits()
        schedule = limits.schedule
        opens = next_open(now, schedule.operator_timezone, schedule)
        if opens > now:
            wakes.append(opens)
        elif await repo.list_leads(["pending"], limit=1):
            wakes.append(await self._lookup(configured, limits, now))
        if not live:
            wakes.extend(await self._start_run(configured, now))
        return min(wakes, default=None)

    async def _lookup(
        self, configured: LeadQueueSettings, limits: OutreachLimits, now: datetime
    ) -> datetime:
        if self.next_lookup_at is not None and now < self.next_lookup_at:
            return self.next_lookup_at
        day = now.date().isoformat()
        accounts = [a for a in configured.account_ids if self.lookup_rest.get(a) != day]
        checked, resting = (
            await run_lookup_pass(accounts, limits, now, self.cancel) if accounts else (0, set())
        )
        self.lookup_rest.update(dict.fromkeys(resting, day))
        # Nobody had room left today: the allowances refill at midnight UTC.
        self.next_lookup_at = now + lookup_spacing(limits) if checked else next_utc_day(now)
        return self.next_lookup_at

    async def _start_run(self, configured: LeadQueueSettings, now: datetime) -> list[datetime]:
        """Hand the found leads to a new send run; returns when to look again."""
        found = await repo.list_leads(["found"], limit=_MAX_RUN_LEADS)
        resting = {a: until for a, until in self.send_rest.items() if until > now}
        leads: list[LeadQueueItem] = []
        accounts: dict[str, None] = {}
        for lead in found:
            account_id = lead.account_id
            if account_id is None or lead.user_id is None or account_id in resting:
                continue
            if account_id not in accounts and len(accounts) >= _MAX_RUN_ACCOUNTS:
                continue
            accounts[account_id] = None
            leads.append(lead)
        if not leads:
            return [min(resting.values())] if found and resting else []
        run_id = uuid4().hex
        for lead in leads:
            lead.status, lead.run_id = "messaging", run_id
        await repo.save_leads(leads)
        request = BulkMessageRequest(
            account_ids=list(accounts),
            recipients=[str(lead.user_id) for lead in leads],
            text=configured.text,
            mode="split",
            recipient_accounts={str(lead.user_id): str(lead.account_id) for lead in leads},
            recipient_phones={str(lead.user_id): lead.phone for lead in leads},
        )
        try:
            await bulk_messages.start_bulk_message_job(
                request, configured.owner_user_id or "", kind="lead_queue", run_id=run_id
            )
        except ValueError:
            await self._back_to_found(leads)
            raise
        bulk_messages.launch_bulk_message_job(run_id)
        return [now + timedelta(seconds=SYNC_SECONDS)]

    @staticmethod
    async def _back_to_found(leads: list[LeadQueueItem]) -> None:
        for lead in leads:
            lead.status, lead.run_id = "found", None
        await repo.save_leads(leads)

    async def sync(self, now: datetime) -> None:
        """Copy each send's outcome onto its lead; unsent leads of an ended run wait again."""
        by_run: dict[str, list[LeadQueueItem]] = {}
        for lead in await repo.list_leads(["messaging"]):
            by_run.setdefault(str(lead.run_id), []).append(lead)
        for run_id, leads in by_run.items():
            record = await runs_repo.get_bulk_run(run_id)
            if record is None:
                # Marked for a run that was never stored: nothing was sent.
                await self._back_to_found(leads)
                continue
            items = {
                item.recipient: item
                for item in await runs_repo.list_bulk_run_items(run_id)
                if not item.handed_over
            }
            changed = [
                lead
                for lead in leads
                if self._settle(lead, items.get(str(lead.user_id)), record, now)
            ]
            await repo.save_leads(changed)

    def _settle(
        self,
        lead: LeadQueueItem,
        item: BulkRunItem | None,
        record: BulkRunRecord,
        now: datetime,
    ) -> bool:
        ended = record.status != "running"
        if item is None or item.status in {"pending", "sending"}:
            if not ended:
                return False
            if item is not None and item.status == "sending":
                lead.status, lead.error_code = "unconfirmed", "delivery_unconfirmed"
            else:
                lead.status, lead.run_id = "found", None
            return True
        lead.finished_at = item.finished_at
        if item.status == "ok":
            lead.status = "sent"
        elif item.status == "unconfirmed":
            lead.status, lead.error_code = "unconfirmed", item.error_code
        elif item.error_code in STOP_CODES:
            # The account provably sent nothing and cannot send for now: retry later.
            lead.status, lead.run_id, lead.finished_at = "found", None, None
            rest = (
                now + timedelta(seconds=item.retry_after_seconds)
                if item.retry_after_seconds
                else next_utc_day(now)
            )
            self.send_rest[item.account_id] = max(rest, self.send_rest.get(item.account_id, now))
        else:
            lead.status, lead.error_code = "failed", item.error_code
        return True
