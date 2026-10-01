"""Row mapping and the shared state predicates of the scheduled-post repository."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError
from sqlalchemy import or_, select

from core.repositories.scheduled_posts._tables import (
    _scheduled_post_media,
    _scheduled_profile_posts,
)
from schemas.scheduled_posts import ScheduledPost, ScheduledStoryOptions

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import ColumnElement, RowMapping
    from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

posts = _scheduled_profile_posts
media = _scheduled_post_media
# Rows whose files may go: nothing can follow these two states.
RELEASED_STATES = ("done", "cancelled")
FINAL_STATES = ("done", "cancelled", "failed", "missed", "ambiguous")
_OPERATOR_EDITABLE = ("pending", "missed", "failed", "ambiguous")


def now_unix() -> int:
    return int(time.time())


def queued() -> ColumnElement[bool]:
    return (posts.c.state == "processing") & (posts.c.stage == "queued")


def dispatching() -> ColumnElement[bool]:
    return (posts.c.state == "processing") & (posts.c.stage == "dispatching")


def operator_editable() -> ColumnElement[bool]:
    """Rows a cancel / reschedule may take: idle ones, or claimed but not yet sent."""
    return or_(posts.c.state.in_(_OPERATOR_EDITABLE), queued())


def _to_post(row: RowMapping, media_names: list[str]) -> ScheduledPost:
    story_json = row["story_json"]
    return ScheduledPost(
        post_id=str(row["post_id"]),
        account_id=str(row["account_id"]),
        kind=row["kind"],
        state=row["state"],
        stage=row["stage"],
        scheduled_for_unix=int(cast("int", row["scheduled_for_unix"])),
        next_attempt_unix=int(cast("int", row["next_attempt_unix"])),
        attempts=int(cast("int", row["attempts"])),
        media_names=media_names,
        filename=cast("str | None", row["filename"]),
        story=(
            ScheduledStoryOptions.model_validate(json.loads(str(story_json)))
            if story_json is not None
            else None
        ),
        batch_id=cast("str | None", row["batch_id"]),
        client_key=cast("str | None", row["client_key"]),
        error_code=cast("str | None", row["error_code"]),
        story_id=cast("int | None", row["story_id"]),
        created_at=str(row["created_at"]),
        updated_unix=int(cast("int", row["updated_unix"])),
        finished_unix=cast("int | None", row["finished_unix"]),
    )


def load(connection: Connection, rows: Sequence[RowMapping]) -> list[ScheduledPost]:
    """Rows → models, with every post's media names in one extra query.

    A row that no longer validates (its stored story options predate a tightened
    schema, or it was edited by hand) is left out rather than raised: one bad row
    must not take down the list, the cancel, or the publisher of every account.
    The claim settles such a row ``failed`` so it stops being due.
    """
    ids = [str(row["post_id"]) for row in rows]
    names: dict[str, list[str]] = {post_id: [] for post_id in ids}
    if ids:
        media_rows = connection.execute(
            select(media.c.post_id, media.c.media_name)
            .where(media.c.post_id.in_(ids))
            .order_by(media.c.post_id, media.c.position),
        ).all()
        for post_id, media_name in media_rows:
            names[str(post_id)].append(str(media_name))
    loaded = []
    for row in rows:
        try:
            loaded.append(_to_post(row, names[str(row["post_id"])]))
        except (ValidationError, ValueError):
            logger.warning("scheduled post %s no longer validates; skipped", row["post_id"])
    return loaded


def fetch_in(connection: Connection, post_id: str) -> ScheduledPost | None:
    row = connection.execute(select(posts).where(posts.c.post_id == post_id)).mappings().first()
    loaded = [] if row is None else load(connection, [row])
    return loaded[0] if loaded else None
