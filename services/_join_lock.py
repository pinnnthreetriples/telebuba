"""One mutex per account over the join budget both features spend.

Neuroshilling and neurocomment charge the same counter — ``neurocomment_join_log``,
counted per ACCOUNT over a rolling 24 hours — and each of them reads that count,
dispatches the join and charges it across three separate awaits. Nothing serialised
that stretch, so joins of one account that overlapped in time all read a count none
of the others had charged yet, and the cap only held while every RPC finished inside
the pause that happened to separate them.

**Not the pacer.** ``services.pacing.await_send_slot`` releases its own lock before
it returns: it spaces a queue and holds nothing once a slot has been granted.

**Not ``services.warming.account_lock``.** That is the account LIFECYCLE mutex —
Start, Stop, Promote, Handoff and remove_account all take it — and the section this
one covers spans a paced Telegram write, which is the exact thing ``services.pacing``
documents must never be awaited inside it. The nesting is therefore one-way: this
lock is taken OUTSIDE ``account_lock``, which the gateway seams take beneath it.

**Two chargers of three take it.** ``services.neurocomment._join.run_join_pass``
charges the same log with the listener account and stays outside, so the cap can still
slip by one join while that pass overlaps a campaign holding that account — and nothing
refuses a listener account a neuroshilling roster.

ponytail: one uvicorn worker, so an in-process ``asyncio.Lock`` reaches every join that
takes it. A second worker would not share the map, and the cap would then need the count
and the charge to be one atomic statement in SQL instead.
"""

from __future__ import annotations

import asyncio

# One lock per account, made on demand. The dict needs no lock of its own: one worker
# means one event loop, and a function with no ``await`` in it cannot be straddled by a
# second caller. Not persisted, and cleared between tests — an ``asyncio.Lock`` binds to
# the loop that first waits on it, so one left behind breaks the next test on that id.
_LOCKS: dict[str, asyncio.Lock] = {}


def join_lock(account_id: str) -> asyncio.Lock:
    """The mutex for this account's [read the cap -> join -> charge it] section."""
    lock = _LOCKS.get(account_id)
    if lock is None:
        lock = _LOCKS[account_id] = asyncio.Lock()
    return lock


def reset_for_tests() -> None:
    _LOCKS.clear()
