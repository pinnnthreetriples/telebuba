"""Next-day re-checks of numbers a contact lookup answered "not on Telegram".

A miss is not proof: an account under a silent import limit answers every number
as "not found", and a fresh one may still see the person. So a run's final
``not_found`` and ``recheck`` answers are queued (``contact_rechecks``) for the next
UTC day, and the next lookup run takes the due ones into whatever import allowance
its accounts have left after the operator's own numbers, on an account other than
the one that missed. Its answer settles the number: found, or a second account's
"not found", ends the re-check; a number no other selected account has room for
stays queued for a later run.

The queue lives in the database rather than in a timer because lookups are run by
the operator: the re-check happens inside a run they start, within its pacing and
daily allowances, and survives a restart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.repositories.recipient_history import (
    count_contact_rechecks,
    drop_contact_rechecks,
    list_contact_rechecks,
    queue_contact_recheck,
)
from schemas.contact_lookup import ContactRecheckSummary
from schemas.recipient_history import ContactRecheck
from services.accounts._bulk_plan import next_utc_day
from services.accounts._contact_lookup_lanes import Lane
from services.accounts._recipient_history import phone_key

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from schemas.contact_lookup import ContactLookupJob


async def load_contact_rechecks() -> dict[str, ContactRecheck]:
    """Every queued number by its digits, the earliest due first."""
    return {recheck.phone_key: recheck for recheck in await list_contact_rechecks()}


def place_rechecks(
    lanes: Sequence[Lane],
    queued: Iterable[ContactRecheck],
    phones: Sequence[str],
    now: datetime,
) -> list[str]:
    """Put the due re-checks into lanes with room left, never on the account that missed.

    A number the operator typed again is checked as typed; it still settles below.
    """
    typed = {phone_key(phone) for phone in phones}
    placed: list[str] = []
    for recheck in queued:
        if recheck.due_at > now.isoformat() or recheck.phone_key in typed:
            continue
        others = [
            lane for lane in lanes if lane.account_id != recheck.checked_by and lane.spare() > 0
        ]
        lane = max(others, key=Lane.spare, default=None)
        if lane is None:
            continue
        lane.queue.append(recheck.phone)
        # Another account already missed it: a second miss says nothing about this one.
        lane.rechecks.add(recheck.phone)
        placed.append(recheck.phone)
    return placed


async def settle_rechecks(
    job: ContactLookupJob,
    queued: Mapping[str, ContactRecheck],
    *,
    enabled: bool,
    now: datetime,
) -> None:
    """Close the re-checks this run answered and queue its own misses when enabled."""
    done: list[str] = []
    for outcome in job.results:
        key = phone_key(outcome.phone)
        if outcome.handed_over or key is None:
            continue
        earlier = queued.get(key)
        if earlier is not None and outcome.account_id != earlier.checked_by:
            outcome.rechecked = outcome.status in {"found", "not_found"}
        if outcome.status == "found" or (outcome.rechecked and outcome.status == "not_found"):
            if earlier is not None:
                done.append(key)
            continue
        if outcome.status in {"not_found", "recheck"} and earlier is None and enabled:
            await queue_contact_recheck(
                ContactRecheck(
                    phone_key=key,
                    phone=outcome.phone,
                    checked_by=outcome.account_id,
                    due_at=next_utc_day(now).isoformat(),
                    created_at=now.isoformat(),
                )
            )
    await drop_contact_rechecks(done)


async def summarize_contact_rechecks() -> ContactRecheckSummary:
    """How many numbers wait for a re-check, and how many a run would take in now."""
    waiting, due = await count_contact_rechecks(datetime.now(UTC).isoformat())
    return ContactRecheckSummary(waiting=waiting, due=due)
