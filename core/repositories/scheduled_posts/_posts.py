"""Operator-facing scheduled-post access: create, read, cancel, reschedule, sweep.

Every write is a compare-and-set ``UPDATE ... WHERE <expected state>`` whose
rowcount says whether THIS caller won: the worker and the operator race on the
same row, and a read-then-write would let both win.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from core.db import _get_engine, _now_iso
from core.repositories.scheduled_posts._rows import (
    FINAL_STATES,
    RELEASED_STATES,
    fetch_in,
    load,
    media,
    now_unix,
    operator_editable,
    posts,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.engine import Connection

    from schemas.scheduled_posts import ScheduledPost


def _fetch_post(post_id: str) -> ScheduledPost | None:
    with _get_engine().connect() as connection:
        return fetch_in(connection, post_id)


async def fetch_post(post_id: str) -> ScheduledPost | None:
    return await asyncio.to_thread(_fetch_post, post_id)


def _keyed(
    connection: Connection,
    batch_id: str | None,
    account_id: str,
    client_key: str | None,
) -> ScheduledPost | None:
    if batch_id is None or client_key is None:
        return None
    row = (
        connection.execute(
            select(posts).where(
                (posts.c.batch_id == batch_id)
                & (posts.c.account_id == account_id)
                & (posts.c.client_key == client_key),
            ),
        )
        .mappings()
        .first()
    )
    # ``load`` skips a row that no longer validates; that is "nothing to answer".
    loaded = [] if row is None else load(connection, [row])
    return loaded[0] if loaded else None


def _fetch_keyed(batch_id: str, account_id: str, client_key: str) -> ScheduledPost | None:
    with _get_engine().connect() as connection:
        return _keyed(connection, batch_id, account_id, client_key)


async def fetch_keyed(batch_id: str, account_id: str, client_key: str) -> ScheduledPost | None:
    """The row a keyed bulk request already created, if any."""
    return await asyncio.to_thread(_fetch_keyed, batch_id, account_id, client_key)


def _keyed_duplicate(connection: Connection, post: ScheduledPost) -> ScheduledPost | None:
    return _keyed(connection, post.batch_id, post.account_id, post.client_key)


def _try_insert(post: ScheduledPost) -> tuple[ScheduledPost, bool] | None:
    with _get_engine().begin() as connection:
        existing = _keyed_duplicate(connection, post)
        if existing is not None:
            return existing, False
        try:
            connection.execute(
                insert(posts).values(
                    **post.model_dump(exclude={"media_names", "story"}),
                    story_json=None if post.story is None else post.story.model_dump_json(),
                ),
            )
        except IntegrityError:
            return None
        connection.execute(
            insert(media),
            [
                {"post_id": post.post_id, "position": position, "media_name": name}
                for position, name in enumerate(post.media_names)
            ],
        )
        return post, True


def _insert_post(post: ScheduledPost) -> tuple[ScheduledPost, bool] | None:
    inserted = _try_insert(post)
    if inserted is not None:
        return inserted
    # An IntegrityError is one of two things: a concurrent identical bulk request
    # won the unique key (answer its row), or the account row vanished under the
    # foreign key (answer None, the caller's not-found).
    with _get_engine().connect() as connection:
        existing = _keyed_duplicate(connection, post)
    return None if existing is None else (existing, False)


async def insert_post(post: ScheduledPost) -> tuple[ScheduledPost, bool] | None:
    """Return ``(row, created)``, or None when the account no longer exists.

    A keyed bulk retry answers the row it already made.
    """
    return await asyncio.to_thread(_insert_post, post)


def _count_open_for_account(account_id: str) -> int:
    with _get_engine().connect() as connection:
        return int(
            connection.execute(
                select(func.count())
                .select_from(posts)
                .where(
                    (posts.c.account_id == account_id)
                    & posts.c.state.in_(("pending", "processing")),
                ),
            ).scalar_one(),
        )


async def count_open_for_account(account_id: str) -> int:
    return await asyncio.to_thread(_count_open_for_account, account_id)


def _list_account_posts(account_id: str, final_since_unix: int) -> list[ScheduledPost]:
    with _get_engine().connect() as connection:
        rows = (
            connection.execute(
                select(posts)
                .where(
                    (posts.c.account_id == account_id)
                    & (
                        posts.c.state.in_(("pending", "processing"))
                        | (posts.c.updated_unix >= final_since_unix)
                    ),
                )
                .order_by(posts.c.scheduled_for_unix, posts.c.post_id),
            )
            .mappings()
            .all()
        )
        return load(connection, rows)


async def list_account_posts(account_id: str, final_since_unix: int) -> list[ScheduledPost]:
    """Open posts, plus the ones that settled at or after ``final_since_unix``."""
    return await asyncio.to_thread(_list_account_posts, account_id, final_since_unix)


def _operator_update(post_id: str, account_id: str, values: Mapping[str, object]) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(posts)
            .where(
                (posts.c.post_id == post_id)
                & (posts.c.account_id == account_id)
                & operator_editable(),
            )
            .values(**values, updated_unix=now_unix()),
        )
        return result.rowcount == 1


async def reschedule_post(post_id: str, account_id: str, run_at_unix: int) -> bool:
    values = {
        "state": "pending",
        "stage": "queued",
        "scheduled_for_unix": run_at_unix,
        "next_attempt_unix": run_at_unix,
        "attempts": 0,
        "error_code": None,
        "finished_unix": None,
        "dispatch_started_unix": None,
    }
    return await asyncio.to_thread(_operator_update, post_id, account_id, values)


async def cancel_post(post_id: str, account_id: str) -> bool:
    values = {"state": "cancelled", "finished_unix": now_unix()}
    return await asyncio.to_thread(_operator_update, post_id, account_id, values)


def _referenced_media() -> frozenset[str]:
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(media.c.media_name)
            .join(posts, posts.c.post_id == media.c.post_id)
            .where(posts.c.state.not_in(RELEASED_STATES))
            .distinct(),
        ).all()
    return frozenset(str(row[0]) for row in rows)


async def referenced_media() -> frozenset[str]:
    """Every stored file a post that can still publish (or be rescheduled) needs."""
    return await asyncio.to_thread(_referenced_media)


def _purge_settled_before(cutoff_unix: int) -> int:
    with _get_engine().begin() as connection:
        return int(
            connection.execute(
                delete(posts).where(
                    posts.c.state.in_(FINAL_STATES) & (posts.c.updated_unix < cutoff_unix),
                ),
            ).rowcount,
        )


async def purge_settled_before(cutoff_unix: int) -> int:
    """Drop settled rows untouched since ``cutoff_unix`` (their media rows cascade)."""
    return await asyncio.to_thread(_purge_settled_before, cutoff_unix)


def new_post_timestamps() -> tuple[str, int]:
    """``(created_at, updated_unix)`` for a row about to be inserted."""
    return _now_iso(), now_unix()
