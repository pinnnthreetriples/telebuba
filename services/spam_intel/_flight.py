"""Single-flight (design 5.5 rule 3): one probe per account at a time, shared only when it is real.

Callers that arrive while a probe of the account is in flight, forced or not, wait for it and
share its answer, so five "check channels" callers cost one question. What they may share is
narrow: the verdict of a REAL probe (``served_from == "probe"``). Everything else the leader
returns belongs to the request that produced it: a cache hit or a passive answer would hand a
forced, measurement or operator ask the very thing it exists to get past, and a budget deferral
was decided for the leader's purpose (a routine ask waits out a quiet start, a decision does
not). Such a joiner waits for the leader to finish and then asks for itself under the budget, and
so does one whose leader failed or was cancelled (it left no verdict).

In-memory on purpose: one uvicorn worker makes it sound, and a restart has nothing in flight.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemas.spam_status import SpamStatusVerdict

# account id -> the leader's future; its value is the leader's answer, or ``None`` when the
# leader ended without one (it failed or was cancelled).
INFLIGHT: dict[str, asyncio.Future[SpamStatusVerdict | None]] = {}


async def join(account_id: str) -> SpamStatusVerdict | None:
    """Wait out the probes in flight for the account; a real probe's verdict, else ``None``.

    ``None`` means no probe is in flight any more and the caller must lead. Nothing yields
    between this returning and :func:`open_flight`, so two such callers cannot both lead.
    """
    while (leader := INFLIGHT.get(account_id)) is not None:
        shared = await asyncio.shield(leader)
        if shared is not None and shared.served_from == "probe":
            return shared.model_copy(update={"served_from": "coalesced"})
    return None


def open_flight(account_id: str) -> asyncio.Future[SpamStatusVerdict | None]:
    """Become the account's leader."""
    future: asyncio.Future[SpamStatusVerdict | None] = asyncio.get_running_loop().create_future()
    INFLIGHT[account_id] = future
    return future


def close_flight(
    account_id: str,
    future: asyncio.Future[SpamStatusVerdict | None],
    result: SpamStatusVerdict | None,
) -> None:
    """End the leadership and release the waiters, with the leader's answer or ``None``."""
    INFLIGHT.pop(account_id, None)
    future.set_result(result)
