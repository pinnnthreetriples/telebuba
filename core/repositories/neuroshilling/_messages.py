"""The send journal — one row per ``(run_id, target, step_id)``, written BEFORE the send.

**The order is the whole point.** The unique index only protects rows that EXIST, so
a run that sends first and writes afterwards leaves a window in which Telegram has
the message and SQLite has nothing: the next boot finds no row, replays the step, and
a stranger's chat gets the line twice. :func:`claim_message` therefore inserts
``pending`` before anything is dispatched and :func:`settle_message` moves the row to
its outcome afterwards — the same order ``core.repositories.neurocomment._comments``
uses for its claim, and the same ``on_conflict_do_nothing`` that makes a concurrent or
resumed second attempt a no-op instead of a duplicate.

**Nothing in this module deletes a ``pending`` row.** ``services.neuroshilling`` leaves
one behind on purpose whenever the dispatch was already on the wire when the connection
died (``UNCONFIRMED_ERROR_TYPE``): Telegram may well have applied it, so the row has to
keep holding its key. :func:`fail_pending_messages` at boot moves those to ``failed``
rather than removing them for exactly that reason — the row goes on occupying the key
and the resumed run skips the step, which is what
``core.repositories.neurocomment._comment_lifecycle`` does with surviving claims.

One write does delete journal rows: ``_scenario._drop_steps_beyond`` removes them for
steps a shortened scenario no longer has. It cannot reach a run in flight, because
``campaigns.refuse_while_live`` refuses every scenario write while the campaign is
live — which is what keeps the durability claim above true for as long as it matters.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._tables import _neuroshilling_messages, run_scope

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingMessageStatus, NeuroshillingStepKey

_TABLE = _neuroshilling_messages
# The row the boot sweep writes over an interrupted dispatch. A class NAME, like every
# other ``error_type`` in the domain, because this column is read back by the board.
_INTERRUPTED = "InterruptedRun"


def _at(key: NeuroshillingStepKey) -> ColumnElement[bool]:
    """The WHERE clause naming exactly one journal row."""
    return (
        (_TABLE.c.run_id == key.run_id)
        & (_TABLE.c.target == key.target)
        & (_TABLE.c.step_id == key.step_id)
    )


def _claim_message(
    key: NeuroshillingStepKey,
    campaign_id: str,
    account_id: str,
    text: str,
    status: NeuroshillingMessageStatus,
) -> bool:
    statement = (
        sqlite_insert(_TABLE)
        .values(
            campaign_id=campaign_id,
            run_id=key.run_id,
            target=key.target,
            step_id=key.step_id,
            account_id=account_id,
            text=text,
            status=status,
            created_at=_now_iso(),
        )
        .on_conflict_do_nothing(
            index_elements=[_TABLE.c.run_id, _TABLE.c.target, _TABLE.c.step_id],
        )
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def claim_message(
    key: NeuroshillingStepKey,
    *,
    campaign_id: str,
    account_id: str,
    text: str,
    status: NeuroshillingMessageStatus = "pending",
) -> bool:
    """Reserve one step of one target for this run. ``False`` means it is already taken.

    Called BEFORE the dispatch, and the ``False`` is what makes a resumed run skip a
    step it already played: the row survives whatever happened to the process, so the
    key stays occupied.

    ``status`` is parametrised for the one caller that knows before it inserts that
    nothing will be sent — a step the account's quota refuses — so a skip reserves its
    key through THIS insert rather than a second one that would have to re-earn the
    same conflict guarantee. Copied from ``_comments._claim_comment``.
    """
    return await asyncio.to_thread(_claim_message, key, campaign_id, account_id, text, status)


def _settle_message(
    key: NeuroshillingStepKey,
    status: NeuroshillingMessageStatus,
    message_id: int | None,
    error_type: str | None,
) -> bool:
    values: dict[str, object] = {
        "status": status,
        "message_id": message_id,
        "error_type": error_type,
    }
    if status == "sent":
        values["sent_at"] = _now_iso()
    statement = (
        update(_TABLE)
        # Only a row this run is still holding. A settle that arrived after the boot
        # sweep already wrote the step off must not resurrect it.
        .where(_at(key) & (_TABLE.c.status == "pending"))
        .values(**values)
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def settle_message(
    key: NeuroshillingStepKey,
    *,
    status: NeuroshillingMessageStatus,
    message_id: int | None = None,
    error_type: str | None = None,
) -> bool:
    """Record what became of a claimed step. ``False`` means the claim was gone.

    ``message_id`` is what the reply chain and the reaction steps are aimed at, so a
    ``sent`` row without one is a step nothing can answer — which is why the anchor
    walk treats an empty id and a missing row identically.
    """
    return await asyncio.to_thread(_settle_message, key, status, message_id, error_type)


def _fetch_message_id(key: NeuroshillingStepKey) -> int | None:
    statement = select(_TABLE.c.message_id).where(_at(key) & (_TABLE.c.status == "sent"))
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    return None if row is None else row[0]


async def fetch_message_id(key: NeuroshillingStepKey) -> int | None:
    """The id a delivered step got IN THIS TARGET, or ``None``.

    The key is the whole triple and never the step alone — see
    :class:`schemas.neuroshilling.NeuroshillingStepKey` for what splitting it costs.
    """
    return await asyncio.to_thread(_fetch_message_id, key)


def _hand_over_message(key: NeuroshillingStepKey, account_id: str) -> bool:
    statement = (
        update(_TABLE)
        .where(_at(key) & (_TABLE.c.status == "failed"))
        .values(account_id=account_id, status="pending", error_type=None)
    )
    with _get_engine().begin() as connection:
        return connection.execute(statement).rowcount > 0


async def hand_over_message(key: NeuroshillingStepKey, *, account_id: str) -> bool:
    """Give one FAILED row to another account so the step can be sent again.

    An UPDATE and never a delete-then-insert: ``(run_id, target, step_id)`` is unique
    and this row already holds that key, so handing it over never opens a window in
    which the step looks unplayed. Only a ``failed`` row moves — a ``pending`` one is
    a dispatch whose outcome is unknown, a ``sent`` one is published, and a
    ``skipped`` one was refused before anything reached Telegram.

    Back to ``pending`` because that is what the row means again: reserved, not yet
    settled. Whatever happens next settles it exactly as the first attempt would
    have, and a process that dies in between is swept into ``failed`` by
    :func:`fail_pending_messages` like any other interrupted dispatch.
    """
    return await asyncio.to_thread(_hand_over_message, key, account_id)


def _list_journalled_steps(run_id: str) -> set[tuple[str, str]]:
    statement = select(_TABLE.c.target, _TABLE.c.step_id).where(_TABLE.c.run_id == run_id)
    with _get_engine().connect() as connection:
        return {(str(target), str(step_id)) for target, step_id in connection.execute(statement)}


async def list_journalled_steps(run_id: str) -> set[tuple[str, str]]:
    """Every ``(target, step_id)`` this run has already written a row for.

    Read once when a run starts so a resumed pass walks past finished work instead of
    sleeping through each step's delay only to lose the insert at the end of it.
    """
    return await asyncio.to_thread(_list_journalled_steps, run_id)


def _last_revive_cycle(run_id: str) -> int:
    statement = select(_TABLE.c.run_id).where(_TABLE.c.run_id.like(f"{run_id}#%")).distinct()
    with _get_engine().connect() as connection:
        rows = [str(row[0]) for row in connection.execute(statement)]
    return max((int(row.removeprefix(f"{run_id}#")) for row in rows), default=0)


async def last_revive_cycle(run_id: str) -> int:
    """The highest cycle number ``run_id`` has journalled under; 0 if it has none.

    A revive cycle writes its rows under ``f"{run_id}#{n}"``. A resumed run that
    counted from zero again would therefore aim cycle 1 at keys cycle 1 already
    filled: :func:`claim_message` refuses every one of them, the cycle publishes
    nothing, and it still pays each step's delay, the listening window and the
    pause before the next one — for as many cycles as the killed process managed.
    Counting on from here is what gives the resumed run keys nothing holds.

    A cycle that journalled no row at all leaves no number behind and is handed out
    a second time, which costs nothing: none of its keys is held.

    The suffix parses because ``_revive._cycle_context`` is the only writer of a
    ``#`` into this column, and it writes an ``int``. The ``LIKE`` needs no escaping
    for the reason ``_tables.run_scope`` gives.
    """
    return await asyncio.to_thread(_last_revive_cycle, run_id)


def _fail_pending_messages(run_id: str) -> int:
    statement = (
        update(_TABLE)
        .where(run_scope(run_id) & (_TABLE.c.status == "pending"))
        .values(status="failed", error_type=_INTERRUPTED)
    )
    with _get_engine().begin() as connection:
        return int(connection.execute(statement).rowcount)


async def fail_pending_messages(run_id: str) -> int:
    """Settle the rows a killed process left mid-flight; return how many there were.

    Updated rather than deleted, and that is the entire point: a ``pending`` row is
    either a dispatch that never finished or one whose outcome is unknown, and both
    must go on occupying their key so the resumed run does not play the step again.

    Scoped with ``run_scope``, so a revive campaign's per-cycle keys are swept too.
    Matching the plain id alone left every interrupted cycle ``pending`` for ever,
    and a ``pending`` row counts against the account's quota until something settles
    it — a campaign that loops all day would have throttled itself to a stop.
    """
    return await asyncio.to_thread(_fail_pending_messages, run_id)


def _list_sent_message_ids(campaign_id: str, target: str) -> set[int]:
    statement = select(_TABLE.c.message_id).where(
        (_TABLE.c.campaign_id == campaign_id)
        & (_TABLE.c.target == target)
        & (_TABLE.c.status == "sent")
        & _TABLE.c.message_id.is_not(None),
    )
    with _get_engine().connect() as connection:
        return {int(row[0]) for row in connection.execute(statement)}


async def list_sent_message_ids(campaign_id: str, target: str) -> set[int]:
    """Every message id THIS CAMPAIGN put into ``target``, across all its runs.

    Half of what the chat poller needs to answer "is this one of ours?" honestly.
    Telethon's ``out`` flag only answers it for the account doing the reading, so a
    line said by a sibling account of the same campaign looks like a stranger's — and
    the fleet would then quote its own scripted dialogue back into its own prompt and
    offer to answer it. Only half, because this table holds SCENARIO steps: an
    autoreply answers no step and has no row here at all, which is why
    ``services.neuroshilling._autoreply`` records its own published answers straight
    into the chat log instead.

    Not scoped to the current run: the earlier runs' messages are still sitting in
    that chat, and they are just as much ours.
    """
    return await asyncio.to_thread(_list_sent_message_ids, campaign_id, target)
