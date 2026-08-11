"""Retention purge for append-only neurocomment history and the durable post inbox.

The three tables were only ever emptied when a whole campaign was deleted, so an
always-on deployment grew them forever and every per-post quota read paid for it.
This mirrors the warming retention purges (``purge_logs_older_than`` and friends):
one cutoff in, the number of rows removed out — deliberately an ``int``, not a new
Pydantic model, so nothing in ``schemas/`` has to change for a maintenance counter.

All three deletes share one transaction: retention is all-or-nothing bookkeeping,
and a partial purge would leave the caller's single "removed" count lying. Public
functions wrap sync helpers via ``asyncio.to_thread`` (non-negotiable #2).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete

from core.db import _get_engine
from core.repositories.neurocomment._tables import (
    _neurocomment_challenges,
    _neurocomment_comments,
    _neurocomment_inbox,
    _neurocomment_join_log,
)


def _purge_neurocomment_history_older_than(cutoff_iso: str) -> int:
    statements = (
        # Settled rows only. A row still ``claimed`` is an in-flight post claim: deleting
        # it would free the (channel, post_id) primary key for a duplicate comment (the
        # claim IS the idempotency guard) and rob the startup stale-claim reclaim of the
        # row it releases. Old claims are settled by that reclaim, then purged here.
        delete(_neurocomment_comments).where(
            (_neurocomment_comments.c.created_at < cutoff_iso)
            & (_neurocomment_comments.c.status.in_(("posted", "failed"))),
        ),
        # ``solved`` rows are NOT audit ballast — the ``WHERE outcome='solved'`` projection
        # over this table IS the global decision cache that
        # ``services.neurocomment.challenge.lookup_cached_decision`` consults before paying
        # for a fresh LLM call. Purging them by age would silently evict the cache and make
        # every later account re-solve (and re-pay for) a challenge we already answered, so
        # they are kept forever regardless of the window.
        delete(_neurocomment_challenges).where(
            (_neurocomment_challenges.c.decided_at < cutoff_iso)
            & (_neurocomment_challenges.c.outcome != "solved"),
        ),
        # Ballast only because the caller keeps ``cutoff_iso`` at least a day old (see the
        # floor in ``services.neurocomment._sweep._prune_history_if_due``): the log backs a
        # rolling-24h per-account join count, so a cutoff INSIDE that window would make the
        # count under-report and let an account exceed the #270 anti-freeze join cap. Do not
        # call this with a sub-day cutoff.
        delete(_neurocomment_join_log).where(_neurocomment_join_log.c.joined_at < cutoff_iso),
        # Completed/expired inbox rows are only an overlap/restart dedup journal. The
        # backfill TTL is at most one day and retention is floored to one day, so a row is
        # never forgotten while the bounded history reader could surface it again.
        delete(_neurocomment_inbox).where(
            (_neurocomment_inbox.c.received_at < cutoff_iso)
            & (_neurocomment_inbox.c.state.in_(("done", "expired"))),
        ),
    )
    with _get_engine().begin() as connection:
        return sum(connection.execute(statement).rowcount for statement in statements)


async def purge_neurocomment_history_older_than(cutoff_iso: str) -> int:
    """Delete settled neurocomment history older than ``cutoff_iso``; returns rows removed.

    In-flight comment claims and cached ``solved`` challenge decisions are never
    touched — see the per-statement comments for why each exclusion is load-bearing.
    """
    return await asyncio.to_thread(_purge_neurocomment_history_older_than, cutoff_iso)
