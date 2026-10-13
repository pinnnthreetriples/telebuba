"""The loop that drives one persistent bulk-message run to its end.

It keeps the run's items and accounts in memory and writes each change through
``core.repositories.bulk_runs``, so a restart picks up from the rows. Every pass it
looks for the first pending item whose account may send now — not stopped, not
already sending, its own pause or series break over, a first message left in its
UTC-day cap, and the recipient's window open — and starts it, up to
``concurrency`` sends at once. With nothing sendable it sleeps until the earliest
moment something becomes sendable, and says why on the run row.

A daily cap no longer drops a recipient: the account waits for the next UTC day.
An account that hits a stop code is done for the run: each mode skips its
remaining recipients, split mode hands them to the accounts still live.
"""

from __future__ import annotations

import asyncio
import random
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.repositories import bulk_runs as repo
from core.repositories.outreach import add_outreach_usage, list_outreach_usage
from schemas.outreach_limits import OutreachDailyUsage
from services.accounts._bulk_plan import next_open, next_utc_day

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from schemas.bulk_messages import BulkMessageOutcome, BulkMessageRequest
    from schemas.bulk_runs import BulkRunAccount, BulkRunItem, WaitReason
    from schemas.run_schedule import RunSchedule

    Send = Callable[[str, str, str, str], Awaitable[BulkMessageOutcome]]

# Statuses where the message may have left the account.
SENT_STATUSES = frozenset({"ok", "unconfirmed"})
# A wait shorter than this is not worth a row write for the progress view.
_REPORTED_WAIT = timedelta(seconds=1)


def _now() -> datetime:
    """Seam: the runner's clock."""
    return datetime.now(UTC)


async def _sleep(wakeup: asyncio.Event, seconds: float | None) -> None:
    """Seam: wait ``seconds`` (forever when ``None``) or until ``wakeup`` fires."""
    with suppress(TimeoutError):
        await asyncio.wait_for(wakeup.wait(), timeout=seconds)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


@dataclass
class Lane:
    row: BulkRunAccount
    sending: bool = False
    on_break: bool = False
    # First messages left in ``day``; ``None`` when the run has no daily cap.
    left: int | None = None
    day: str = ""

    def ready_at(self) -> datetime | None:
        return None if self.row.ready_at is None else datetime.fromisoformat(self.row.ready_at)


@dataclass
class _Candidate:
    wake_at: datetime | None = None
    reason: WaitReason | None = None

    def offer(self, moment: datetime, reason: WaitReason) -> None:
        if self.wake_at is None or moment < self.wake_at:
            self.wake_at, self.reason = moment, reason


@dataclass
class BulkRun:
    run_id: str
    data: BulkMessageRequest
    schedule: RunSchedule
    items: list[BulkRunItem]
    lanes: dict[str, Lane]
    send: Send
    cancel: asyncio.Event
    start_at: datetime | None
    seq: int = 0
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    reported: tuple[str | None, str | None] = (None, None)
    # The first error a send task died with; the loop re-raises it.
    failure: BaseException | None = None

    async def run(self) -> bool:
        """Send until nothing is pending or the run is cancelled; True when cancelled.

        A failed send task stops the run with its error. If this loop itself is
        cancelled (shutdown), the sends in flight are cancelled and awaited first: their
        items stay ``sending`` in the database, and a restart marks them unconfirmed.
        """
        try:
            return await self._run()
        except asyncio.CancelledError:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            raise

    async def _run(self) -> bool:
        while not self.cancel.is_set():
            if self.failure is not None:
                raise self.failure
            # Cleared before looking, never after: a send that ends while this pass
            # awaits the database must still wake the sleep below.
            self.wakeup.clear()
            now = _now()
            await self._refresh_caps(now)
            item, wait = self._pick(now)
            if item is not None:
                await self._report(None, None)
                self._start(item)
                continue
            # ``lane.sending`` drops before the wake-up fires; the task set only empties
            # a loop turn later, so it cannot tell that the last send is over.
            sending = any(lane.sending for lane in self.lanes.values())
            if not sending and not any(i.status == "pending" for i in self.items):
                break
            await self._report(wait.wake_at, wait.reason)
            delay = None if wait.wake_at is None else max((wait.wake_at - now).total_seconds(), 0)
            await _sleep(self.wakeup, delay)
        if self.tasks:
            await asyncio.gather(*self.tasks)
        await self._report(None, None)
        if not self.cancel.is_set():
            return False
        await self._undo_unsent_hand_overs()
        return any(i.status == "pending" for i in self.items)

    async def _undo_unsent_hand_overs(self) -> None:
        """A hand-over the cancel cut off never happened: the refusal is the final word."""
        for retry in [i for i in self.items if i.status == "pending"]:
            if retry.id is None:
                continue
            refused = next(
                (
                    i
                    for i in self.items
                    if i.handed_over
                    and i.recipient == retry.recipient
                    and i.ordinal == retry.ordinal
                ),
                None,
            )
            if refused is None:
                continue
            refused.handed_over = False
            await repo.save_bulk_run_item(refused)
            await repo.delete_bulk_run_item(retry.id)
            self.items.remove(retry)

    def _pick(self, now: datetime) -> tuple[BulkRunItem | None, _Candidate]:
        wait = _Candidate()
        if self.start_at is not None and self.start_at > now:
            wait.offer(self.start_at, "schedule")
            return None, wait
        if sum(lane.sending for lane in self.lanes.values()) >= self.schedule.concurrency:
            return None, wait
        for item in self.items:
            if item.status != "pending":
                continue
            lane = self.lanes[item.account_id]
            if lane.sending or lane.row.stop_code is not None:
                continue
            if lane.left is not None and lane.left <= 0:
                spare = self._spare_lane(item, now)
                if spare is not None:
                    # Split mode: an account out of today's cap passes the recipient on.
                    item.account_id = spare
                    return item, wait
                wait.offer(next_utc_day(now), "daily_cap")
                continue
            ready = lane.ready_at()
            if ready is not None and ready > now:
                wait.offer(ready, "series_break" if lane.on_break else "pause")
                continue
            opens = next_open(now, item.tz_name, self.schedule)
            if opens > now:
                wait.offer(opens, "hours")
                continue
            return item, wait
        return None, wait

    def _spare_lane(self, item: BulkRunItem, now: datetime) -> str | None:
        """A live account that may send this unpinned split recipient right now."""
        if self.data.mode != "split" or item.recipient in self.data.recipient_accounts:
            return None
        if next_open(now, item.tz_name, self.schedule) > now:
            return None
        for account_id in self.data.account_ids:
            lane = self.lanes[account_id]
            ready = lane.ready_at()
            if (
                lane.row.stop_code is None
                and not lane.sending
                and (lane.left is None or lane.left > 0)
                and (ready is None or ready <= now)
            ):
                return account_id
        return None

    async def _report(self, wake_at: datetime | None, reason: WaitReason | None) -> None:
        shown = (
            (None, None)
            if wake_at is None or wake_at - _now() < _REPORTED_WAIT
            else (_iso(wake_at), reason)
        )
        if shown != self.reported:
            self.reported = shown
            await repo.update_bulk_run(self.run_id, next_send_at=shown[0], wait_reason=shown[1])

    async def _refresh_caps(self, now: datetime) -> None:
        if self.data.daily_cap is None:
            return
        day = now.date().isoformat()
        stale = [account_id for account_id, lane in self.lanes.items() if lane.day != day]
        if not stale:
            return
        usage = await list_outreach_usage(stale, day)
        for account_id in stale:
            lane = self.lanes[account_id]
            lane.day = day
            lane.left = self.data.daily_cap - usage[account_id].first_messages

    def _start(self, item: BulkRunItem) -> None:
        lane = self.lanes[item.account_id]
        lane.sending = True
        item.status = "sending"
        task = asyncio.create_task(self._send(item, lane))
        self.tasks.add(task)
        task.add_done_callback(self._settled)

    def _settled(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None and self.failure is None:
            self.failure = task.exception()
            self.wakeup.set()

    async def _send(self, item: BulkRunItem, lane: Lane) -> None:
        try:
            try:
                await repo.save_bulk_run_item(item)
            except Exception:
                # Nothing went out: the recipient is still to be written to.
                item.status = "pending"
                raise
            outcome = await self.send(item.account_id, item.recipient, item.peer, self.data.text)
            try:
                await self._record(item, lane, outcome)
            except Exception:
                # The message may have left: never send it again.
                item.status = "unconfirmed"
                raise
        finally:
            lane.sending = False
            self.wakeup.set()

    async def _finish(self, item: BulkRunItem, **fields: object) -> None:
        self.seq += 1
        for name, value in fields.items():
            setattr(item, name, value)
        item.finished_seq = self.seq
        item.finished_at = _iso(_now())
        item.id = await repo.save_bulk_run_item(item)

    async def _record(self, item: BulkRunItem, lane: Lane, outcome: BulkMessageOutcome) -> None:
        now = _now()
        await self._finish(
            item,
            status=outcome.status,
            error_code=outcome.error_code,
            retry_after_seconds=outcome.retry_after_seconds,
        )
        lane.on_break = False
        pause = random.uniform(  # noqa: S311  # nosec B311 - timing jitter, not a secret
            self.data.min_delay_seconds or 0, self.data.max_delay_seconds or 0
        )
        ready = now + timedelta(seconds=pause)
        if outcome.status in SENT_STATUSES:
            await add_outreach_usage(
                OutreachDailyUsage(
                    account_id=item.account_id, day=now.date().isoformat(), first_messages=1
                )
            )
            if lane.left is not None:
                lane.left -= 1
            lane.row.series_count += 1
            if self.schedule.series_size and lane.row.series_count >= self.schedule.series_size:
                lane.row.series_count = 0
                lane.on_break = True
                rest = random.uniform(  # noqa: S311  # nosec B311 - timing jitter, not a secret
                    self.schedule.series_break_min_seconds, self.schedule.series_break_max_seconds
                )
                ready = max(ready, now + timedelta(seconds=rest))
        lane.row.ready_at = _iso(ready)
        if outcome.error_code in STOP_CODES:
            await self._stop(lane, item, outcome)
        await repo.save_bulk_run_account(lane.row)

    async def _stop(self, lane: Lane, refused: BulkRunItem, outcome: BulkMessageOutcome) -> None:
        lane.row.stop_code = outcome.error_code
        lane.row.retry_after_seconds = outcome.retry_after_seconds
        stopped = lane.row.account_id
        pending = [i for i in self.items if i.status == "pending" and i.account_id == stopped]
        if self.data.mode == "each":
            for item in pending:
                await self._skip(item, outcome)
            return
        pinned = self.data.recipient_accounts
        rotation = self._rotation_after(stopped)
        if refused.recipient not in pinned and self._any_live():
            # The refused row stays as history; the next live account gets a fresh one.
            refused.handed_over = True
            await repo.save_bulk_run_item(refused)
            retry = refused.model_copy(
                update={
                    "id": None,
                    "account_id": next(rotation),
                    "status": "pending",
                    "handed_over": False,
                    "error_code": None,
                    "retry_after_seconds": None,
                    "finished_seq": None,
                    "finished_at": None,
                }
            )
            self.items.insert(self.items.index(refused) + 1, retry)
            retry.id = await repo.save_bulk_run_item(retry)
        for item in pending:
            if item.recipient in pinned or not self._any_live():
                await self._skip(item, outcome)
                continue
            item.account_id = next(rotation)
            await repo.save_bulk_run_item(item)

    def _any_live(self) -> bool:
        return any(lane.row.stop_code is None for lane in self.lanes.values())

    def _rotation_after(self, account_id: str) -> Iterator[str]:
        """Live accounts in rotation order after ``account_id``; drawn only while one is live."""
        order = self.data.account_ids
        start = order.index(account_id)
        while True:
            for offset in range(1, len(order) + 1):
                candidate = order[(start + offset) % len(order)]
                if self.lanes[candidate].row.stop_code is None:
                    yield candidate

    async def _skip(self, item: BulkRunItem, outcome: BulkMessageOutcome) -> None:
        await self._finish(
            item,
            status="skipped",
            error_code=outcome.error_code,
            retry_after_seconds=outcome.retry_after_seconds,
        )


# Codes where the account provably sent nothing and cannot send for the rest of the
# run: stop using it (split hands the recipient on). "unconfirmed" never lands here.
STOP_CODES = frozenset(
    {
        "flood_wait",
        "slow_mode_wait",
        "premium_wait",
        "peer_flood",
        "account_not_found",
        "session_dead",
        "account_deactivated",
        "account_frozen",
        "unavailable",
    }
)
