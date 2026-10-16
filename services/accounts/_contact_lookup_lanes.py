"""Per-account lanes of a contact-lookup run: planning, outcomes and hand-over.

A lane is one account's queue of numbers plus what the run has learnt about it.
Its ``capacity`` is the account's daily import allowance left at the start of the
run, so planning never gives a lane more than it may submit today, and a hand-over
only goes to a lane that still has room.

Silent limit: after an import ban Telegram does not refuse ``ImportContacts``, it
answers every number as "not on Telegram". A lane whose last ``streak_limit``
checked numbers all came back empty is treated as silently limited when the
account found people before (in this run or on any stored day) or another lane of
the run is still finding people — a list where nobody is on Telegram trips nothing.
Its streak is re-labelled "needs re-check" and, with its unchecked numbers, handed
to lanes with room; what does not fit stays re-check / not checked. A re-checked
number that misses again does not grow the receiver's streak.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from schemas.contact_lookup import ContactLookupOutcome
from services.accounts._outreach_protection import LIMIT_CODES

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from schemas.contact_lookup import ContactLookupJob
    from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch


@dataclass
class Lane:
    account_id: str
    capacity: int
    found_before: bool = False
    queue: deque[str] = field(default_factory=deque)
    # Loop time before which this account sends nothing (its own pause).
    ready_at: float = 0.0
    found: int = 0
    # Consecutive "not on Telegram" numbers since this lane's last match, oldest first.
    streak: list[str] = field(default_factory=list)
    # Numbers handed over for a re-check: one already looked empty to another account,
    # so a second miss says nothing about this one.
    rechecks: set[str] = field(default_factory=set)
    stopped: bool = False
    # The Telegram limit (code, wait) that stopped this lane, if one did.
    limit: tuple[str, int | None] | None = None

    def spare(self) -> int:
        return self.capacity - len(self.queue)

    def take(self, size: int) -> list[str]:
        """Pop the next batch; it counts against the lane's capacity from now on."""
        batch = [self.queue.popleft() for _ in range(min(size, len(self.queue)))]
        self.capacity -= len(batch)
        return batch


def plan_lanes(
    phones: Sequence[str],
    account_ids: Sequence[str],
    allowances: Mapping[str, int],
    found_before: set[str],
) -> tuple[list[Lane], list[tuple[str, str]]]:
    """Spread phones round-robin over lanes with room; return the ``(account, phone)`` overflow.

    With room everywhere this is plain round-robin, one account per number. A full
    lane passes its turn on; a phone no lane can take is reported against the
    account whose turn it was.
    """
    lanes = [
        Lane(account_id, max(allowances[account_id], 0), found_before=account_id in found_before)
        for account_id in account_ids
    ]
    overflow: list[tuple[str, str]] = []
    cursor = 0
    for index, phone in enumerate(phones):
        for step in range(len(lanes)):
            lane = lanes[(cursor + step) % len(lanes)]
            if lane.spare() > 0:
                lane.queue.append(phone)
                cursor = (cursor + step + 1) % len(lanes)
                break
        else:
            overflow.append((account_ids[index % len(account_ids)], phone))
    return lanes, overflow


def _outcome(account_id: str, phone: str, match: ContactLookupMatch | None) -> ContactLookupOutcome:
    if match is None:
        return ContactLookupOutcome(phone=phone, account_id=account_id, status="not_found")
    display_name = " ".join(part for part in (match.first_name, match.last_name) if part) or None
    return ContactLookupOutcome(
        phone=phone,
        account_id=account_id,
        status="found",
        user_id=match.user_id,
        username=match.username,
        display_name=display_name,
    )


def skip_phones(
    account_id: str,
    phones: Iterable[str],
    job: ContactLookupJob,
    *,
    error_code: str,
    retry_after: int | None = None,
) -> None:
    for phone in phones:
        job.results.append(
            ContactLookupOutcome(
                phone=phone,
                account_id=account_id,
                status="skipped",
                error_code=error_code,
                retry_after_seconds=retry_after,
            )
        )
        job.completed += 1


def stop_lane(
    lane: Lane,
    extra: Sequence[str],
    job: ContactLookupJob,
    *,
    error_code: str,
    retry_after: int | None,
) -> None:
    """Skip ``extra`` and the lane's queue; the lane sends nothing more this run."""
    lane.stopped = True
    if error_code in LIMIT_CODES:
        lane.limit = (error_code, retry_after)
    skip_phones(
        lane.account_id,
        [*extra, *lane.queue],
        job,
        error_code=error_code,
        retry_after=retry_after,
    )
    lane.queue.clear()


def record_batch(
    lane: Lane,
    batch: Sequence[str],
    batch_result: ContactLookupBatchResult,
    job: ContactLookupJob,
) -> list[str]:
    """Record every checked phone of one batch; return the ones Telegram deferred."""
    found = {match.phone: match for match in batch_result.matches}
    retry = set(batch_result.retry)
    deferred: list[str] = []
    for phone in batch:
        if phone in retry:
            deferred.append(phone)
            continue
        match = found.get(phone)
        job.results.append(_outcome(lane.account_id, phone, match))
        job.completed += 1
        if match is None:
            if phone not in lane.rechecks:
                lane.streak.append(phone)
        else:
            lane.found += 1
            lane.streak.clear()
    return deferred


def looks_silently_limited(lane: Lane, lanes: Sequence[Lane], streak_limit: int) -> bool:
    if len(lane.streak) < streak_limit:
        return False
    if lane.found_before or lane.found:
        return True
    return any(other.found for other in lanes if other is not lane)


def _receiver(lane: Lane, lanes: Sequence[Lane]) -> Lane | None:
    candidates = [o for o in lanes if o is not lane and not o.stopped and o.spare() > 0]
    return max(candidates, key=Lane.spare, default=None)


def hand_over_silent_lane(lane: Lane, lanes: Sequence[Lane], job: ContactLookupJob) -> None:
    """Stop a silently limited lane, re-label its streak and hand its numbers on."""
    lane.stopped = True
    suspects = set(lane.streak)
    unchecked = list(lane.queue)
    lane.queue.clear()
    lane.streak = []
    for outcome in job.results:
        if (
            outcome.account_id != lane.account_id
            or outcome.phone not in suspects
            or outcome.status != "not_found"
        ):
            continue
        outcome.status = "recheck"
        outcome.error_code = "silent_limit"
        receiver = _receiver(lane, lanes)
        if receiver is not None:
            # The receiver's answer becomes the final one; this row stays as history.
            receiver.queue.append(outcome.phone)
            receiver.rechecks.add(outcome.phone)
            outcome.handed_over = True
            job.completed -= 1
    for phone in unchecked:
        receiver = _receiver(lane, lanes)
        if receiver is None:
            skip_phones(lane.account_id, [phone], job, error_code="silent_limit")
        else:
            receiver.queue.append(phone)
