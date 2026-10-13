"""Journal rows → stints. Pure: no I/O, no clock.

An account id can come back after a deletion (a re-import), so the journal of one id
is first cut into *lives* at each ``deleted`` row. Inside a life a stint opens at a
``warming_started`` row whose stint start differs from the open one; a resume or a
restart that carried the clock repeats the same start and opens nothing. A row that
names a stint start nobody opened (its start fell out of the log retention before the
journal existed) opens that stint too. Rows before any stint — an account that was
never warmed — belong to no stint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from schemas.lifecycle import LifecycleEvent


def parse_moment(raw: str | None) -> datetime | None:
    """An ISO timestamp as an aware UTC moment; ``None`` when absent or unreadable."""
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Stint:
    """One warming stint of one account life, with the rows observed during it."""

    account_id: str
    started_at: datetime
    # The stint start exactly as the journal spells it (the cohort fallback matches on it).
    start_key: str
    # Rows from the stint's opening up to the next stint of the same life.
    events: tuple[LifecycleEvent, ...]
    # Every row of the life, for the old-deletion guess (was it ever limited?).
    life_events: tuple[LifecycleEvent, ...]
    # The next stint's start in the same life: observation of this one stops there.
    cut_at: datetime | None
    # The last stint of a life no ``deleted`` row has ended: the account's current one.
    current: bool


def split_lives(events: Iterable[LifecycleEvent]) -> list[list[LifecycleEvent]]:
    """One account's rows (id order) cut after each ``deleted`` row."""
    lives: list[list[LifecycleEvent]] = []
    current: list[LifecycleEvent] = []
    for event in events:
        current.append(event)
        if event.kind == "deleted":
            lives.append(current)
            current = []
    if current:
        lives.append(current)
    return lives


def _opening_key(event: LifecycleEvent, open_key: str | None) -> str | None:
    """The stint start this row opens, or ``None`` when it opens nothing."""
    key = event.stint_started_at
    if event.kind == "warming_started" and key is None:
        key = event.created_at
    if key is None or key == open_key:
        return None
    return key


def _openings(life: list[LifecycleEvent]) -> list[tuple[int, str, datetime]]:
    openings: list[tuple[int, str, datetime]] = []
    open_key: str | None = None
    for index, event in enumerate(life):
        key = _opening_key(event, open_key)
        moment = parse_moment(key)
        if key is not None and moment is not None:
            openings.append((index, key, moment))
            open_key = key
    return openings


def life_stints(life: list[LifecycleEvent]) -> list[Stint]:
    """The stints of one life, oldest first."""
    openings = _openings(life)
    live = not life or life[-1].kind != "deleted"
    stints: list[Stint] = []
    for position, (index, key, started_at) in enumerate(openings):
        following = openings[position + 1] if position + 1 < len(openings) else None
        stop = following[0] if following else len(life)
        stints.append(
            Stint(
                account_id=life[index].account_id,
                started_at=started_at,
                start_key=key,
                events=tuple(life[index:stop]),
                life_events=tuple(life),
                cut_at=following[2] if following else None,
                current=live and following is None,
            ),
        )
    return stints


def build_stints(events: Iterable[LifecycleEvent]) -> list[Stint]:
    """Every stint in the journal, grouped per account id in journal order."""
    by_account: dict[str, list[LifecycleEvent]] = {}
    for event in sorted(events, key=lambda row: row.id):
        by_account.setdefault(event.account_id, []).append(event)
    return [
        stint
        for rows in by_account.values()
        for life in split_lives(rows)
        for stint in life_stints(life)
    ]
