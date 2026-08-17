"""Presence side of the neuroshilling repository: which account is inside which target.

Membership is a property of the PAIR, not of the target, and that is the whole
reason this table exists. A numeric chat id is resolved out of an account's own
session entity cache — one SQLite file per account — so the id account A got back
means nothing to account B, which answers ``ValueError: Could not find the input
entity`` instead. The only cure for a private supergroup is for B to actually join
it, so the join outcome has to be recorded per (account, target) or every restart
re-plays joins it already made and every substitution posts into a chat it was
never in. ``services.neuroshilling._telegram.join_target`` is the reader that makes
that true: it asks :func:`fetch_presence_state` before every join, and a table
nobody read would have bought exactly nothing.

No ``chat_id`` column, deliberately: the run keeps the resolved ids in memory and a
restart pays one RPC per pair to rebuild them, whereas a stored id would outlive
the session that could use it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._tables import (
    _neuroshilling_accounts,
    _neuroshilling_presence,
)
from schemas.neuroshilling import NeuroshillingPresence

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import RowMapping

    from schemas.neuroshilling import NeuroshillingPresenceState

_TABLE = _neuroshilling_presence
# Everything :class:`NeuroshillingPresence` needs, and not ``campaign_id``: the model
# is always read inside one campaign's question.
_PRESENCE_COLUMNS: Final = (
    _TABLE.c.account_id,
    _TABLE.c.target,
    _TABLE.c.state,
    _TABLE.c.last_error_type,
    _TABLE.c.joined_at,
    _TABLE.c.updated_at,
)
# Verdicts about the ACCOUNT that happen to be stored on a pair's row. They answer
# for every target that account was going to play, including the ones it has no row
# for yet — which is the only reason this read looks past the pair it was asked about.
_ACCOUNT_WIDE_STATES: Final = ("flooded", "retired")
# The one of them that EXPIRES. Telegram's rate limit is a wait; the 500-chat ceiling
# and a dead session are not. ``updated_at`` is when the flood was recorded, and the
# caller supplies the cutoff, so a row older than it no longer answers for anything.
_EXPIRING_STATE: Final = "flooded"


def _row_to_presence(mapping: RowMapping) -> NeuroshillingPresence:
    return NeuroshillingPresence.model_validate(dict(mapping))


def _record_presence(
    campaign_id: str,
    account_id: str,
    target: str,
    state: NeuroshillingPresenceState,
    error_type: str | None,
) -> None:
    now = _now_iso()
    # Stamped only when the pair actually gets in, and never cleared afterwards: a
    # later flood or ban is a change of state, not proof the join never happened.
    joined_at = now if state == "joined" else None
    values = {
        "campaign_id": campaign_id,
        "account_id": account_id,
        "target": target,
        "state": state,
        "last_error_type": error_type,
        "joined_at": joined_at,
        "updated_at": now,
    }
    update_set = {"state": state, "last_error_type": error_type, "updated_at": now}
    if joined_at is not None:
        update_set["joined_at"] = joined_at
    statement = (
        sqlite_insert(_TABLE)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[_TABLE.c.campaign_id, _TABLE.c.account_id, _TABLE.c.target],
            set_=update_set,
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def record_presence(
    campaign_id: str,
    account_id: str,
    target: str,
    state: NeuroshillingPresenceState,
    *,
    error_type: str | None = None,
) -> None:
    """Write where one account stands with one target.

    ``joined_at`` survives a later state change on purpose — the operator's question
    is "when did this account get in", and blanking it on the first flood would erase
    the only answer.
    """
    await asyncio.to_thread(
        _record_presence,
        campaign_id,
        account_id,
        target,
        state,
        error_type,
    )


def _list_presence(campaign_id: str, target: str | None) -> list[NeuroshillingPresence]:
    statement = select(*_PRESENCE_COLUMNS).where(_TABLE.c.campaign_id == campaign_id)
    if target is not None:
        statement = statement.where(_TABLE.c.target == target)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement.order_by(_TABLE.c.target, _TABLE.c.account_id))
        return [_row_to_presence(row._mapping) for row in rows]  # noqa: SLF001 - Row API


async def list_presence(
    campaign_id: str,
    *,
    target: str | None = None,
) -> Sequence[NeuroshillingPresence]:
    """Every presence row of a campaign, or only those for one target."""
    return await asyncio.to_thread(_list_presence, campaign_id, target)


def _fetch_presence_state(
    campaign_id: str,
    account_id: str,
    target: str,
    flood_since: str,
) -> NeuroshillingPresenceState | None:
    statement = select(*_PRESENCE_COLUMNS).where(
        (_TABLE.c.account_id == account_id)
        & (
            _TABLE.c.state.in_(_ACCOUNT_WIDE_STATES)
            | ((_TABLE.c.campaign_id == campaign_id) & (_TABLE.c.target == target))
        ),
    )
    with _get_engine().connect() as connection:
        rows = [_row_to_presence(row._mapping) for row in connection.execute(statement)]  # noqa: SLF001 - Row API
    # Applied to the pair's own row as well as to the account-wide ones: a chat this
    # account was flooded out of an hour ago is a chat it may try again.
    live = [row for row in rows if row.state != _EXPIRING_STATE or row.updated_at >= flood_since]
    stored = next((row for row in live if row.state in _ACCOUNT_WIDE_STATES), None) or next(
        (row for row in live if row.target == target),
        None,
    )
    return stored.state if stored is not None else None


async def fetch_presence_state(
    campaign_id: str,
    account_id: str,
    target: str,
    *,
    flood_since: str,
) -> NeuroshillingPresenceState | None:
    """The stored verdict that already answers a join of this pair, or ``None``.

    An account-wide verdict outranks the pair's own row and is honoured whichever
    campaign wrote it, because that is the scope Telegram applied it at: an account
    flooded while joining one target must not join the NEXT one either, and that
    target has no row of its own to carry the verdict.

    ``flood_since`` is where that scope stops: a ``flooded`` row older than it answers
    for nothing. The verdict has no other way back — ``retire_account_presence`` only
    rewrites live pairs — so an unbounded one turned a thirty-second wait into
    permanent retirement from every campaign.
    """
    return await asyncio.to_thread(
        _fetch_presence_state,
        campaign_id,
        account_id,
        target,
        flood_since,
    )


def _list_halted_accounts(campaign_id: str, flood_since: str) -> list[str]:
    statement = (
        select(_neuroshilling_accounts.c.account_id)
        .distinct()
        .select_from(
            _neuroshilling_accounts.join(
                _TABLE,
                _TABLE.c.account_id == _neuroshilling_accounts.c.account_id,
            ),
        )
        .where(
            (_neuroshilling_accounts.c.campaign_id == campaign_id)
            & _TABLE.c.state.in_(_ACCOUNT_WIDE_STATES)
            & ((_TABLE.c.state != _EXPIRING_STATE) | (_TABLE.c.updated_at >= flood_since)),
        )
        .order_by(_neuroshilling_accounts.c.account_id)
    )
    with _get_engine().connect() as connection:
        return [str(account_id) for (account_id,) in connection.execute(statement)]


async def list_halted_accounts(campaign_id: str, *, flood_since: str) -> list[str]:
    """This campaign's roster accounts carrying an account-wide verdict still in force.

    The roster narrows it, the verdict does not: the join gate reads ``flooded`` and
    ``retired`` whichever campaign wrote them, so a card scoped to its own campaign's
    presence rows under-reported exactly the accounts that will refuse to play.
    """
    return await asyncio.to_thread(_list_halted_accounts, campaign_id, flood_since)


def _retire_account_presence(
    account_id: str,
    state: NeuroshillingPresenceState,
) -> int:
    statement = (
        update(_TABLE)
        .where(
            (_TABLE.c.account_id == account_id)
            # A pair we never got into stays as it is: it carries its own refusal,
            # and overwriting that with an account-wide verdict would lose the only
            # record of WHY this particular target said no.
            & _TABLE.c.state.in_(("pending", "joined")),
        )
        .values(state=state, updated_at=_now_iso())
    )
    with _get_engine().begin() as connection:
        return int(connection.execute(statement).rowcount)


async def retire_account_presence(
    account_id: str,
    state: NeuroshillingPresenceState,
) -> int:
    """Move every live pair of ``account_id`` to an account-wide verdict; return the count.

    Telegram's rate limits and its 500-chat ceiling are properties of the ACCOUNT, so
    a single ``flood_wait`` in one chat says nothing about that chat and everything
    about the account. Persisting it across the account's whole roster is what lets
    the verdict outlive the process: an in-memory halt set would forget it on the next
    restart and start the same account posting again inside its own flood window.

    No ``campaign_id``, and that is the point: an account is not exclusive to one
    campaign — the roster's key is ``(campaign_id, account_id)`` and nothing refuses a
    second campaign the same account — so a per-campaign sweep left it fully live in
    the other one, working through the same ceiling or the same flood.
    """
    return await asyncio.to_thread(_retire_account_presence, account_id, state)
