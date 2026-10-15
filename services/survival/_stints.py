"""Journal rows → account lives and their waves. Pure: no I/O, no clock.

An account id can come back after a deletion (a re-import), so the journal of one id
is first cut into *lives* at each ``deleted`` row. Inside a life a wave (one warming
stint) opens at a ``warming_started`` row whose stint start differs from the open one;
a restart that carried the clock repeats the same start and opens nothing. A row that
names a stint start nobody opened (its start fell out of the log retention before the
journal existed) opens that stint too. Rows before the first wave — an account that
was never warmed — belong to no wave and do not make a life count.

The wave number is the one the start row recorded (``extra.wave``), else one past the
previous wave of the life: rows from before waves were numbered still count in order.
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
    """One wave: a warming stint of one account life, with the rows observed during it."""

    account_id: str
    started_at: datetime
    # The stint start exactly as the journal spells it (the cohort fallback matches on it).
    start_key: str
    wave: int
    # Rows from the wave's opening up to the next wave of the same life.
    events: tuple[LifecycleEvent, ...]


@dataclass(frozen=True)
class Life:
    """One account life that was warmed at least once: its waves and its rows."""

    account_id: str
    waves: tuple[Stint, ...]
    # Every row from the first wave's opening to the life's end, in journal order.
    events: tuple[LifecycleEvent, ...]
    # Every row of the life, the ones before its first wave included.
    all_events: tuple[LifecycleEvent, ...]
    # No ``deleted`` row ended it: the live account's current life.
    current: bool

    @property
    def started_at(self) -> datetime:
        """When the life's first wave began: the life's day 0."""
        return self.waves[0].started_at


def split_lives(
    events: Iterable[LifecycleEvent],
    born_at: str | None = None,
) -> list[list[LifecycleEvent]]:
    """One account's rows (id order) cut after each ``deleted`` row and at ``born_at``.

    The ``deleted`` row is best-effort, so the live account row's creation (``born_at``)
    is a second boundary: rows older than it belong to an earlier life of the id.
    """
    lives: list[list[LifecycleEvent]] = []
    current: list[LifecycleEvent] = []
    for event in events:
        if born_at is not None and current and current[-1].created_at < born_at <= event.created_at:
            lives.append(current)
            current = []
        current.append(event)
        if event.kind == "deleted":
            lives.append(current)
            current = []
    if current:
        lives.append(current)
    return lives


def _opening_key(event: LifecycleEvent, open_key: str | None) -> str | None:
    """The stint start this row opens, or ``None`` when it opens nothing."""
    # Rows rebuilt from the logs (migration 74) cannot tell a restart from a fresh start:
    # each logged start got its own stamp. So backfilled history is one wave at most.
    if event.backfilled and open_key is not None:
        return None
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


def _wave_number(event: LifecycleEvent, previous: int) -> int:
    """The wave the opening row recorded, else the one after ``previous``."""
    recorded = event.extra.get("wave") if event.kind == "warming_started" else None
    if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > previous:
        return recorded
    return previous + 1


def life_waves(life: list[LifecycleEvent]) -> list[Stint]:
    """The waves of one life, oldest first."""
    openings = _openings(life)
    waves: list[Stint] = []
    for position, (index, key, started_at) in enumerate(openings):
        following = openings[position + 1] if position + 1 < len(openings) else None
        stop = following[0] if following else len(life)
        previous = waves[-1].wave if waves else 0
        waves.append(
            Stint(
                account_id=life[index].account_id,
                started_at=started_at,
                start_key=key,
                wave=_wave_number(life[index], previous),
                events=tuple(life[index:stop]),
            ),
        )
    return waves


def build_life(rows: list[LifecycleEvent]) -> Life | None:
    """The life these rows (one life, journal order) describe; ``None`` if never warmed."""
    waves = life_waves(rows)
    if not waves:
        return None
    first = rows.index(waves[0].events[0])
    return Life(
        account_id=rows[0].account_id,
        waves=tuple(waves),
        events=tuple(rows[first:]),
        all_events=tuple(rows),
        current=rows[-1].kind != "deleted",
    )


def build_lives(
    events: Iterable[LifecycleEvent],
    born: dict[str, str] | None = None,
) -> list[Life]:
    """Every warmed life in the journal, grouped per account id in journal order.

    ``born`` maps a live account id to its row's creation, the second life boundary.
    """
    by_account: dict[str, list[LifecycleEvent]] = {}
    for event in sorted(events, key=lambda row: row.id):
        by_account.setdefault(event.account_id, []).append(event)
    births = born or {}
    lives = (
        build_life(rows)
        for account_id, rows_of_id in by_account.items()
        for rows in split_lives(rows_of_id, births.get(account_id))
    )
    return [life for life in lives if life is not None]
