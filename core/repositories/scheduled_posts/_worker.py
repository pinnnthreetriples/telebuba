"""The publisher's side of the scheduled-post rows: claim, dispatch, settle, recover.

``dispatching`` is written BEFORE the Telegram call, so a row found there after a
restart may already be on Telegram; recovery turns it ``ambiguous`` instead of
publishing it a second time.

The sweeps (missed, recovery) read bare ``PostRef`` columns rather than whole
models: one row whose stored story options no longer validate must not stop
the publisher for every other account.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from core.db import _get_engine
from core.repositories.scheduled_posts._rows import (
    dispatching,
    fetch_in,
    now_unix,
    posts,
    queued,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import ColumnElement, Select

    from schemas.scheduled_posts import ScheduledPost, ScheduledPostState

# A settle write that loses to "database is locked" is retried: dropping it would
# leave a published post in ``dispatching`` until the next recovery pass.
_WRITE_ATTEMPTS = 3
_WRITE_RETRY_SECONDS = 0.2


class PostRef(NamedTuple):
    post_id: str
    account_id: str
    kind: str


def _busy_accounts() -> Select[tuple[str]]:
    return select(posts.c.account_id).where(posts.c.state == "processing")


def _mark_missed(cutoff_unix: int) -> list[PostRef]:
    now = now_unix()
    stale = (posts.c.state == "pending") & (posts.c.scheduled_for_unix < cutoff_unix)
    with _get_engine().begin() as connection:
        rows = connection.execute(
            select(posts.c.post_id, posts.c.account_id, posts.c.kind).where(stale)
        ).all()
        missed = []
        for post_id, account_id, kind in rows:
            result = connection.execute(
                update(posts)
                .where((posts.c.post_id == post_id) & (posts.c.state == "pending"))
                .values(state="missed", updated_unix=now, finished_unix=now),
            )
            if result.rowcount == 1:
                missed.append(PostRef(str(post_id), str(account_id), str(kind)))
        return missed


async def mark_missed(cutoff_unix: int) -> list[PostRef]:
    """Pending posts planned before ``cutoff_unix`` become ``missed``."""
    return await asyncio.to_thread(_mark_missed, cutoff_unix)


def _claim_one(at_unix: int) -> ScheduledPost | None:
    with _get_engine().begin() as connection:
        row = (
            connection.execute(
                select(posts.c.post_id)
                .where(
                    (posts.c.state == "pending")
                    & (posts.c.next_attempt_unix <= at_unix)
                    & posts.c.account_id.not_in(_busy_accounts()),
                )
                .order_by(posts.c.next_attempt_unix, posts.c.post_id)
                .limit(1),
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        result = connection.execute(
            update(posts)
            .where((posts.c.post_id == row["post_id"]) & (posts.c.state == "pending"))
            .values(state="processing", stage="queued", updated_unix=now_unix()),
        )
        if result.rowcount != 1:
            return None
        claimed = fetch_in(connection, str(row["post_id"]))
        if claimed is None:
            # It no longer validates: settle it so the queue moves past it.
            now = now_unix()
            connection.execute(
                update(posts)
                .where(posts.c.post_id == row["post_id"])
                .values(
                    state="failed",
                    error_code="scheduled_post_invalid",
                    updated_unix=now,
                    finished_unix=now,
                ),
            )
        return claimed


async def claim_one(at_unix: int) -> ScheduledPost | None:
    """Take the single earliest due post, never two of one account at a time."""
    return await asyncio.to_thread(_claim_one, at_unix)


def _transition(post_id: str, expected: ColumnElement[bool], values: Mapping[str, object]) -> bool:
    for attempt in range(_WRITE_ATTEMPTS):
        try:
            with _get_engine().begin() as connection:
                result = connection.execute(
                    update(posts)
                    .where((posts.c.post_id == post_id) & expected)
                    .values(**values, updated_unix=now_unix()),
                )
                return result.rowcount == 1
        except OperationalError:
            if attempt + 1 == _WRITE_ATTEMPTS:
                raise
            time.sleep(_WRITE_RETRY_SECONDS)
    return False  # pragma: no cover - the loop returns or raises


async def mark_dispatching(post_id: str) -> bool:
    """The point of no return: from here a lost answer means ``ambiguous``."""
    values = {
        "stage": "dispatching",
        "dispatch_started_unix": now_unix(),
        "attempts": posts.c.attempts + 1,
    }
    return await asyncio.to_thread(_transition, post_id, queued(), values)


async def finish_dispatched(
    post_id: str,
    state: ScheduledPostState,
    *,
    error_code: str | None = None,
    story_id: int | None = None,
) -> bool:
    values = {
        "state": state,
        "error_code": error_code,
        "story_id": story_id,
        "finished_unix": now_unix(),
    }
    return await asyncio.to_thread(_transition, post_id, dispatching(), values)


async def retry_dispatched(post_id: str, next_attempt_unix: int, error_code: str) -> bool:
    """Telegram refused WITHOUT applying it (a wait): try again later."""
    values = {
        "state": "pending",
        "stage": "queued",
        "next_attempt_unix": next_attempt_unix,
        "error_code": error_code,
    }
    return await asyncio.to_thread(_transition, post_id, dispatching(), values)


async def settle_queued(post_id: str, state: ScheduledPostState, error_code: str) -> bool:
    """End a claimed post before anything was sent (account gone, file gone...)."""
    values = {"state": state, "error_code": error_code, "finished_unix": now_unix()}
    return await asyncio.to_thread(_transition, post_id, queued(), values)


async def release_unsent(post_id: str, next_attempt_unix: int | None = None) -> bool:
    """Hand a claimed post back to the queue; the CALLER knows it was never sent.

    Either stage qualifies: a cancel can land while ``mark_dispatching`` commits in
    its thread, before the call that would have sent anything.
    """
    values: dict[str, object] = {"state": "pending", "stage": "queued"}
    if next_attempt_unix is not None:
        values["next_attempt_unix"] = next_attempt_unix
    expected = posts.c.state == "processing"
    return await asyncio.to_thread(_transition, post_id, expected, values)


def _requeue_interrupted() -> tuple[int, list[PostRef]]:
    now = now_unix()
    columns = select(posts.c.post_id, posts.c.account_id, posts.c.kind)
    with _get_engine().begin() as connection:
        requeued = connection.execute(
            update(posts).where(queued()).values(state="pending", updated_unix=now),
        ).rowcount
        rows = connection.execute(columns.where(dispatching())).all()
        connection.execute(
            update(posts)
            .where(dispatching())
            .values(
                state="ambiguous",
                error_code="unconfirmed",
                updated_unix=now,
                finished_unix=now,
            ),
        )
    return int(requeued), [PostRef(str(a), str(b), str(c)) for a, b, c in rows]


async def requeue_interrupted() -> tuple[int, list[PostRef]]:
    """Recovery: unsent claims go back, possibly-sent ones become ambiguous.

    Only safe while no post is in flight — at startup, and between two of the
    single worker's posts, when any ``processing`` row is an orphan.
    """
    return await asyncio.to_thread(_requeue_interrupted)


def _next_due_unix() -> int | None:
    # Accounts with a post in ``processing`` are skipped exactly as ``claim_one``
    # skips them, or an unclaimable due post would make the worker spin.
    with _get_engine().connect() as connection:
        value = connection.execute(
            select(func.min(posts.c.next_attempt_unix)).where(
                (posts.c.state == "pending") & posts.c.account_id.not_in(_busy_accounts()),
            ),
        ).scalar_one()
    return None if value is None else int(value)


async def next_due_unix() -> int | None:
    return await asyncio.to_thread(_next_due_unix)


def _photo_due_before(account_id: str, until_unix: int) -> bool:
    with _get_engine().connect() as connection:
        found = connection.execute(
            select(posts.c.post_id)
            .where(
                (posts.c.account_id == account_id)
                & (posts.c.kind == "photo")
                & (posts.c.state == "pending")
                & (posts.c.next_attempt_unix <= until_unix),
            )
            .limit(1),
        ).first()
    return found is not None


async def photo_due_before(account_id: str, until_unix: int) -> bool:
    return await asyncio.to_thread(_photo_due_before, account_id, until_unix)
