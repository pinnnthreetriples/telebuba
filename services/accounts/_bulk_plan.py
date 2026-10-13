"""Planning for a persistent bulk-message run: who sends to whom, and when sending is open.

Pure functions over the request and the clock, so the runner and its tests share
one definition of "inside working hours".
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from core.phone_geo import exact_timezone_for_phone
from schemas.bulk_runs import BulkRunItem, TimeBasis
from schemas.run_schedule import clock_minutes

if TYPE_CHECKING:
    from schemas.bulk_messages import BulkMessageRequest
    from schemas.run_schedule import RunSchedule

_WEEK = 7


def zone_for(phone: str | None, schedule: RunSchedule) -> tuple[str, TimeBasis]:
    """The zone a recipient's window is read in, and whose clock that is.

    The client's own zone only when its phone pins exactly one; a country spanning
    several zones, or a recipient with no phone, falls back to the operator's clock
    and says so, so the operator sees where the run did not know the client's time.
    """
    if schedule.client_timezone and phone:
        zone = exact_timezone_for_phone(phone)
        if zone is not None:
            return zone, "client"
    return schedule.operator_timezone, "operator"


def next_open(now: datetime, tz_name: str, schedule: RunSchedule) -> datetime:
    """``now`` when the window is open in ``tz_name``, else when it next opens (UTC)."""
    zone = ZoneInfo(tz_name)
    local = now.astimezone(zone)
    start = timedelta(minutes=clock_minutes(schedule.work_start))
    end = timedelta(minutes=clock_minutes(schedule.work_end))
    for offset in range(_WEEK + 1):
        day = local.date() + timedelta(days=offset)
        if day.weekday() not in schedule.work_days:
            continue
        midnight = datetime.combine(day, time(0), zone)
        if midnight + start <= local < midnight + end:
            return now
        if midnight + start > local:
            return (midnight + start).astimezone(UTC)
    # Unreachable: ``work_days`` is never empty, so some day of the next week opens.
    return now + timedelta(days=_WEEK)


def next_utc_day(now: datetime) -> datetime:
    """Midnight UTC after ``now``: when the daily caps refill."""
    return datetime.combine(now.date() + timedelta(days=1), time(0), UTC)


def plan_items(
    run_id: str,
    data: BulkMessageRequest,
    schedule: RunSchedule,
    recipients: list[tuple[str, str]],
) -> list[BulkRunItem]:
    """One item per planned send, in the order a single sender would go.

    Each mode: every account writes to every recipient, account by account. Split
    mode: one send per recipient, round-robin over the accounts; a recipient only
    one account can address (``recipient_accounts``) is pinned to it and does not
    take a turn.
    """
    items: list[BulkRunItem] = []

    def add(account_id: str, recipient: str, peer: str) -> None:
        tz_name, basis = zone_for(data.recipient_phones.get(recipient), schedule)
        items.append(
            BulkRunItem(
                run_id=run_id,
                ordinal=len(items),
                account_id=account_id,
                recipient=recipient,
                peer=peer,
                tz_name=tz_name,
                time_basis=basis,
            )
        )

    if data.mode == "each":
        for account_id in data.account_ids:
            for recipient, peer in recipients:
                add(account_id, recipient, peer)
        return items
    cursor = 0
    for recipient, peer in recipients:
        pinned = data.recipient_accounts.get(recipient)
        if pinned is None:
            pinned = data.account_ids[cursor % len(data.account_ids)]
            cursor += 1
        add(pinned, recipient, peer)
    return items
