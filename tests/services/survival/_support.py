"""Synthetic lifecycle journals for the survival tests: rows placed by day number."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import count
from typing import TYPE_CHECKING

from schemas.lifecycle import LifecycleEvent

if TYPE_CHECKING:
    from schemas.lifecycle import LifecycleEventKind

DAY0 = datetime(2026, 9, 7, tzinfo=UTC)  # a Monday: ISO week 37
GRACE = timedelta(hours=36)
_ids = count(1)


def at(day: float) -> datetime:
    """The moment ``day`` days after ``DAY0``."""
    return DAY0 + timedelta(days=day)


def iso(day: float) -> str:
    return at(day).isoformat()


def row(
    kind: LifecycleEventKind,
    day: float,
    *,
    account: str = "acc-1",
    stint: float | None = 0,
    **extra: object,
) -> LifecycleEvent:
    """One journal row on ``day``, in the stint started on day ``stint`` (``None``: none)."""
    return LifecycleEvent(
        id=next(_ids),
        account_id=account,
        stint_started_at=iso(stint) if stint is not None else None,
        kind=kind,
        created_at=iso(day),
        extra=extra,
    )


def released_life(
    release_day: float = 14,
    *,
    account: str = "acc-1",
    start: float = 0,
) -> list[LifecycleEvent]:
    """A stint started on ``start`` and released (target reached) on ``release_day``."""
    return [
        row("warming_started", start, account=account, stint=start, new_stint=True),
        row("warmed", release_day, account=account, stint=start),
    ]


def clean(day: float, *, account: str = "acc-1", stint: float = 0) -> LifecycleEvent:
    return row("spambot_verdict", day, account=account, stint=stint, status="clean")


def limited(day: float, *, account: str = "acc-1", stint: float = 0) -> LifecycleEvent:
    return row("spambot_verdict", day, account=account, stint=stint, status="limited")
