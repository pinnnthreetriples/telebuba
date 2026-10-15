"""The search history per language: its last pool search, and the refill's open request.

Whether the automatic refill is waiting for the operator to give it an account.

Split from ``pool_refill`` so the board can read it without importing the search (which
reaches the runtime lock the board's own import chain sits under).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.repositories.warming_searches import list_channel_searches_since
from schemas._warming_pool import PoolRefillRequest

if TYPE_CHECKING:
    from schemas._warming_pool import ChannelSearchRecord


def refill_interval() -> timedelta:
    """How long one language rests between two pool searches."""
    return timedelta(hours=settings.warming.pool_refill_interval_hours)


def latest_pool_search(
    rows: list[ChannelSearchRecord], language: str
) -> ChannelSearchRecord | None:
    """The newest pool search (not a warming account's own) of ``language`` in ``rows``."""
    pool_rows = [row for row in rows if row.language == language and row.origin != "self"]
    return pool_rows[-1] if pool_rows else None


async def pending_refill_request(languages: list[str]) -> PoolRefillRequest | None:
    """The refill's open request for an account, for the first of these short languages.

    Open while the newest pool search of the language is the refill's ``no_account``
    row: any search that ran since — the operator's consent included — closes it.
    """
    if not settings.warming.pool_refill_enabled or not languages:
        return None
    rows = await list_channel_searches_since(datetime.now(UTC) - refill_interval())
    for language in languages:
        latest = latest_pool_search(rows, language)
        if latest is not None and latest.status == "no_account":
            return PoolRefillRequest(language=language, requested_at=latest.created_at)
    return None
