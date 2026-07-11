"""Cursor-paginated published-comment history (read-only).

The board's feed shows only the last 24h; this powers the "Вся история" modal —
one cursor page of a campaign's ``posted`` comments (newest first, all time).
Mirrors ``services.logs`` pagination: opaque offset cursor + ``limit+1`` probe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.db import list_posted_comments_page
from schemas.api import Page
from services.logs import InvalidCursorError

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord


def _decode_cursor(cursor: str | None) -> int:
    # Opaque offset token (same shape as services.logs); the client never parses it.
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise InvalidCursorError(cursor) from exc
    if offset < 0:
        raise InvalidCursorError(cursor)
    return offset


async def list_comments_page(
    campaign_id: str,
    cursor: str | None = None,
    limit: int = 50,
) -> Page[CommentRecord]:
    """One cursor-paginated page of a campaign's posted comments (newest first)."""
    offset = _decode_cursor(cursor)
    result = await list_posted_comments_page(campaign_id, offset=offset, limit=limit + 1)
    rows = result.comments
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = str(offset + limit) if has_more else None
    return Page(items=items, next_cursor=next_cursor)
