"""One lookup pass of the lead queue: plan, check, record, and catch a silent limit.

A pass gives each account at most one ``ImportContacts`` batch, never more than its
UTC-day allowance has left, so the queue's lookups spread over the working day
instead of spending the allowance at the first tick. The calls, usage counting and
stop codes are the contact lookup's own (``contact_lookup.lookup_in_lanes``).

The silent-limit trap needs a streak longer than one batch, so the queue reads it
from its own rows: an account's newest lookups that all came back "not on
Telegram". When that streak looks like a silent import ban (the lanes module's
rule), those leads go back to pending for another account to re-check, and the
account rests until the next UTC day — as does any account a pass stopped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.repositories import lead_queue as repo
from core.repositories.outreach import list_accounts_with_contact_matches, list_outreach_usage
from services.accounts._contact_lookup_lanes import Lane, looks_silently_limited
from services.accounts.contact_lookup import LookupPacing, lookup_in_lanes

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import datetime

    from schemas.contact_lookup import ContactLookupOutcome
    from schemas.lead_queue import LeadQueueItem
    from schemas.outreach_limits import OutreachLimits

# Pending leads read per pass: enough to fill every lane even past re-check exclusions.
_PENDING_WINDOW = 200
# A lead whose user is already one of these is the same client under a second number.
_CLAIMED = frozenset({"found", "messaging", "sent", "unconfirmed"})


def plan_pass(
    pending: Sequence[LeadQueueItem],
    room: Mapping[str, int],
    found_before: set[str],
) -> list[Lane]:
    """Round-robin the oldest pending leads over the accounts with room.

    A re-check never goes back to the account whose miss looked silently limited.
    """
    lanes = [
        Lane(account_id, capacity, found_before=account_id in found_before)
        for account_id, capacity in room.items()
        if capacity > 0
    ]
    cursor = 0
    for lead in pending:
        if not any(lane.spare() > 0 for lane in lanes):
            break
        for step in range(len(lanes)):
            lane = lanes[(cursor + step) % len(lanes)]
            if lane.spare() > 0 and lane.account_id != lead.recheck_account_id:
                lane.queue.append(lead.phone)
                if lead.recheck_account_id is not None:
                    lane.rechecks.add(lead.phone)
                cursor = (cursor + step + 1) % len(lanes)
                break
    return [lane for lane in lanes if lane.queue]


def _final(results: Iterable[ContactLookupOutcome]) -> dict[str, ContactLookupOutcome]:
    """Each phone's last word: a handed-over row is history, its receiver's row counts."""
    return {outcome.phone: outcome for outcome in results if not outcome.handed_over}


async def _record(
    planned: Sequence[LeadQueueItem],
    results: Sequence[ContactLookupOutcome],
    now: datetime,
) -> None:
    final = _final(results)
    user_ids = {o.user_id for o in final.values() if o.user_id is not None}
    claimed = {
        lead.user_id: lead.id
        for lead in await repo.list_leads_by_user(user_ids)
        if lead.status in _CLAIMED
    }
    stamp = now.isoformat()
    changed: list[LeadQueueItem] = []
    for lead in planned:
        outcome = final.get(lead.phone)
        # Never checked (a daily limit, a stopped account): it simply stays pending.
        if outcome is None or outcome.status == "skipped":
            continue
        if outcome.status == "recheck":
            lead.recheck_account_id, lead.account_id = outcome.account_id, None
        elif outcome.status == "not_found":
            lead.status, lead.account_id = "not_found", outcome.account_id
            lead.looked_up_at = lead.finished_at = stamp
        else:
            lead.account_id, lead.user_id = outcome.account_id, outcome.user_id
            lead.looked_up_at = stamp
            if claimed.get(outcome.user_id, lead.id) != lead.id:
                # The same client under a second number: one first message is enough.
                lead.status, lead.error_code = "skipped", "duplicate_client"
                lead.finished_at = stamp
            else:
                lead.status = "found"
                claimed[outcome.user_id] = lead.id
        changed.append(lead)
    await repo.save_leads(changed)


async def _trailing_misses(account_id: str, limit: int) -> list[LeadQueueItem]:
    """The account's newest lookups that all missed; a re-check miss neither grows nor ends it."""
    streak: list[LeadQueueItem] = []
    for lead in await repo.list_account_history(account_id, limit):
        if lead.status != "not_found":
            break
        if lead.recheck_account_id is None:
            streak.append(lead)
    return streak


async def _catch_silent_limits(lanes: Sequence[Lane], streak_limit: int) -> set[str]:
    """Send each silently limited account's streak back for a re-check; return those accounts."""
    limited: set[str] = set()
    for lane in lanes:
        streak = await _trailing_misses(lane.account_id, streak_limit)
        probe = Lane(
            lane.account_id,
            0,
            found_before=lane.found_before,
            found=lane.found,
            streak=[lead.phone for lead in streak],
        )
        if not looks_silently_limited(probe, lanes, streak_limit):
            continue
        for lead in streak:
            lead.status, lead.recheck_account_id = "pending", lane.account_id
            lead.account_id = lead.looked_up_at = lead.finished_at = None
        await repo.save_leads(streak)
        limited.add(lane.account_id)
    return limited


async def run_lookup_pass(
    account_ids: Sequence[str],
    limits: OutreachLimits,
    now: datetime,
    cancel: asyncio.Event,
) -> tuple[int, set[str]]:
    """Look up one batch per account with room; return (numbers checked, accounts to rest)."""
    pacing = LookupPacing.from_limits(limits)
    usage = await list_outreach_usage(account_ids, now.date().isoformat())
    room = {
        account_id: min(
            pacing.batch_size, limits.contact_imports_per_day - usage[account_id].contact_imports
        )
        for account_id in account_ids
    }
    # Read apart, so re-checks that only their own account could take (it rests, or is
    # the only one configured) never fill the window and starve the fresh numbers.
    fresh = await repo.list_leads(["pending"], limit=_PENDING_WINDOW, recheck=False)
    rechecks = await repo.list_leads(["pending"], limit=_PENDING_WINDOW, recheck=True)
    pending = sorted([*fresh, *rechecks], key=lambda lead: lead.id or 0)
    lanes = plan_pass(pending, room, await list_accounts_with_contact_matches(account_ids))
    if not lanes:
        return 0, set()
    planned_phones = {phone for lane in lanes for phone in lane.queue}
    planned = [lead for lead in pending if lead.phone in planned_phones]
    results = await lookup_in_lanes(lanes, pacing, cancel)
    await _record(planned, results, now)
    resting = {lane.account_id for lane in lanes if lane.stopped}
    resting |= await _catch_silent_limits(lanes, pacing.silent_limit_streak)
    return len(planned_phones), resting
