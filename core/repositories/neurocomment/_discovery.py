"""Channel-discovery candidate persistence (the "Найти каналы" scratch set).

Per-campaign and replaced wholesale on each run — this table is a working set,
not history. The comments-enabled verdict itself is deliberately NOT stored here:
``neurocomment_linked_groups`` already is that cache, globally and persistently,
and a second copy could disagree with it. What IS stored is the *attempt*
(``qualified_at`` / ``qualify_error``), which the cache cannot express — a failed
probe writes nothing there, so without it a candidate would stay pending forever.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import _neurocomment_discovery_candidates
from schemas.neurocomment_discovery import DiscoveryCandidateRow, DiscoveryCandidateRows

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

_TABLE = _neurocomment_discovery_candidates


def _row_to_candidate(row: RowMapping) -> DiscoveryCandidateRow:
    return DiscoveryCandidateRow(
        channel=str(row["channel"]),
        title=str(row["title"] or ""),
        subscribers=None if row["subscribers"] is None else int(row["subscribers"]),
        # Verbatim: the column outlives the code that wrote it, and a row naming a source
        # this build no longer has must reach the board as a label, not as a 500.
        source=str(row["source"]),
        qualified_at=None if row["qualified_at"] is None else str(row["qualified_at"]),
        qualify_error=None if row["qualify_error"] is None else str(row["qualify_error"]),
    )


def _replace_discovery_candidates(campaign_id: str, rows: list[DiscoveryCandidateRow]) -> None:
    now = _now_iso()
    # Delete-then-insert in ONE transaction: a fresh search fully supersedes the
    # previous set, and a crash mid-write must not leave a half-replaced list.
    with _get_engine().begin() as connection:
        connection.execute(delete(_TABLE).where(_TABLE.c.campaign_id == campaign_id))
        if not rows:
            return
        connection.execute(
            _TABLE.insert(),
            [
                {
                    "campaign_id": campaign_id,
                    "channel": row.channel,
                    "title": row.title,
                    "subscribers": row.subscribers,
                    "source": row.source,
                    "qualified_at": row.qualified_at,
                    "qualify_error": row.qualify_error,
                    "created_at": now,
                }
                for row in rows
            ],
        )


async def replace_discovery_candidates(
    campaign_id: str,
    rows: list[DiscoveryCandidateRow],
) -> None:
    """Swap a campaign's candidate set for the results of a fresh search."""
    await asyncio.to_thread(_replace_discovery_candidates, campaign_id, rows)


def _list_discovery_candidates(campaign_id: str) -> DiscoveryCandidateRows:
    statement = select(_TABLE).where(_TABLE.c.campaign_id == campaign_id).order_by(_TABLE.c.channel)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return DiscoveryCandidateRows(rows=[_row_to_candidate(row) for row in rows])


async def list_discovery_candidates(campaign_id: str) -> DiscoveryCandidateRows:
    return await asyncio.to_thread(_list_discovery_candidates, campaign_id)


def _list_pending_discovery_candidates(campaign_id: str) -> DiscoveryCandidateRows:
    statement = (
        select(_TABLE)
        .where(_TABLE.c.campaign_id == campaign_id, _TABLE.c.qualified_at.is_(None))
        .order_by(_TABLE.c.channel)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return DiscoveryCandidateRows(rows=[_row_to_candidate(row) for row in rows])


async def list_pending_discovery_candidates(campaign_id: str) -> DiscoveryCandidateRows:
    """Candidates not probed yet — makes a qualification pass resumable."""
    return await asyncio.to_thread(_list_pending_discovery_candidates, campaign_id)


def _mark_discovery_qualified(
    campaign_id: str,
    channel: str,
    *,
    error: str | None,
    subscribers: int | None,
) -> None:
    values: dict[str, object] = {"qualified_at": _now_iso(), "qualify_error": error}
    # Only overwrite the subscriber count when the probe actually learned one —
    # a failed probe must not wipe the count the search itself returned.
    if subscribers is not None:
        values["subscribers"] = subscribers
    with _get_engine().begin() as connection:
        connection.execute(
            update(_TABLE)
            .where(_TABLE.c.campaign_id == campaign_id, _TABLE.c.channel == channel)
            .values(**values),
        )


async def mark_discovery_qualified(
    campaign_id: str,
    channel: str,
    *,
    error: str | None = None,
    subscribers: int | None = None,
) -> None:
    """Record that this candidate was probed (successfully or not)."""
    await asyncio.to_thread(
        _mark_discovery_qualified,
        campaign_id,
        channel,
        error=error,
        subscribers=subscribers,
    )
