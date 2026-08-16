"""Roster side of the neuroshilling repository: which accounts play a campaign.

The roster is replaced wholesale rather than patched one link at a time — the
page edits it as one card, and a per-link API would leave windows where a link
points at a role the same save removed.

The one write that does patch a single link is :func:`substitute_banned_account`,
and it patches two: a banned account's row and the reserve row promoted into its
role. Both happen inside ONE transaction, and what makes a second caller for the
same ban spend nothing is the conditional ``replaced_by_account_id IS NULL``
update: one ban buys one reserve, whoever asks.

The ban ITSELF is :func:`ban_campaign_account`, and it is deliberately not the
same call. Substitution is conditional on an operator switch and on the target
still being worth playing; the fact that Telegram finished the account off is not
conditional on anything.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import delete, func, insert, select, update

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._tables import (
    _neuroshilling_accounts,
    _neuroshilling_roles,
)
from schemas.neuroshilling import NeuroshillingCampaignAccount

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.engine import Connection

    from schemas.neuroshilling import NeuroshillingAccountAssignment

    # account_id -> (state, replaced_by_account_id), both engine-written.
    _EngineState = dict[str, tuple[str, str | None]]


def _engine_owned_state(connection: Connection, campaign_id: str) -> _EngineState:
    """The half of each roster row the operator's form has no say over."""
    statement = select(
        _neuroshilling_accounts.c.account_id,
        _neuroshilling_accounts.c.state,
        _neuroshilling_accounts.c.replaced_by_account_id,
    ).where(_neuroshilling_accounts.c.campaign_id == campaign_id)
    return {
        str(account_id): (str(state), replaced_by)
        for account_id, state, replaced_by in connection.execute(statement)
    }


def _replace_campaign_accounts(
    connection: Connection,
    campaign_id: str,
    assignments: Sequence[NeuroshillingAccountAssignment],
) -> None:
    """Swap the campaign's whole roster inside the caller's transaction.

    Takes the open connection rather than opening its own so the roster and the
    campaign row it belongs to commit together — a half-applied save would show
    the operator a target list that no longer matches the accounts under it.

    ``state`` and ``replaced_by_account_id`` are engine-written (a ban sets them)
    and are carried forward for every account that survives the replace. Blanking
    them here would reset engine state from a request body just as effectively as
    an editable column would — which is exactly what the schema forbids.
    """
    kept = _engine_owned_state(connection, campaign_id)
    connection.execute(
        delete(_neuroshilling_accounts).where(
            _neuroshilling_accounts.c.campaign_id == campaign_id,
        ),
    )
    # dict.fromkeys dedupes while preserving the operator's order; the composite
    # primary key would otherwise reject a repeated account outright.
    seen = list(dict.fromkeys(item.account_id for item in assignments))
    by_account = {item.account_id: item for item in assignments}
    if not seen:
        return
    now = _now_iso()
    connection.execute(
        insert(_neuroshilling_accounts),
        [
            _roster_row(campaign_id, by_account[account_id], kept.get(account_id), now)
            for account_id in seen
        ],
    )


def _roster_row(
    campaign_id: str,
    assignment: NeuroshillingAccountAssignment,
    engine_state: tuple[str, str | None] | None,
    now: str,
) -> dict[str, object]:
    """One INSERT row: the form's half, plus whatever the engine already knew."""
    state, replaced_by = engine_state or ("active", None)
    return {
        "campaign_id": campaign_id,
        "account_id": assignment.account_id,
        "role_id": assignment.role_id,
        "is_reserve": int(assignment.is_reserve),
        "state": state,
        "replaced_by_account_id": replaced_by,
        "created_at": now,
    }


def _list_campaign_accounts(campaign_id: str) -> list[NeuroshillingCampaignAccount]:
    statement = (
        select(_neuroshilling_accounts)
        .where(_neuroshilling_accounts.c.campaign_id == campaign_id)
        .order_by(
            _neuroshilling_accounts.c.created_at.asc(),
            _neuroshilling_accounts.c.account_id.asc(),
        )
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [NeuroshillingCampaignAccount.model_validate(dict(row)) for row in rows]


async def list_campaign_accounts(campaign_id: str) -> list[NeuroshillingCampaignAccount]:
    """The campaign's roster in assignment order (empty for an unknown campaign)."""
    return await asyncio.to_thread(_list_campaign_accounts, campaign_id)


def _at(campaign_id: str, account_id: str) -> ColumnElement[bool]:
    """The WHERE clause naming exactly one roster row."""
    return (_neuroshilling_accounts.c.campaign_id == campaign_id) & (
        _neuroshilling_accounts.c.account_id == account_id
    )


def _record_ban(connection: Connection, campaign_id: str, account_id: str) -> None:
    """Write the ban onto the roster row. Idempotent, and the first write of the pair.

    Unconditional because it is no longer the claim — ``_dispatch`` has already written
    the same value the moment Telegram said so — but still FIRST, because a write is
    what makes SQLite serialise this transaction against another one for the same pool.
    """
    connection.execute(
        update(_neuroshilling_accounts).where(_at(campaign_id, account_id)).values(state="banned"),
    )


def _claim_replacement(
    connection: Connection,
    campaign_id: str,
    account_id: str,
    reserve: str,
) -> bool:
    """Point the banned row at its stand-in, but only if nothing points there yet.

    This is the claim the ban used to be: one ban buys one reserve, so a second caller
    for the same ban finds the slot filled and leaves the pool alone.
    """
    statement = (
        update(_neuroshilling_accounts)
        .where(
            _at(campaign_id, account_id)
            & (_neuroshilling_accounts.c.replaced_by_account_id.is_(None)),
        )
        .values(replaced_by_account_id=reserve)
    )
    return connection.execute(statement).rowcount > 0


def _ban_campaign_account(campaign_id: str, account_id: str) -> None:
    with _get_engine().begin() as connection:
        _record_ban(connection, campaign_id, account_id)


async def ban_campaign_account(campaign_id: str, account_id: str) -> None:
    """Record that Telegram has finished this account off, whatever happens next.

    Written where the verdict is CLASSIFIED and not where a reserve is promoted,
    because the two are separate questions and only the first one is always asked: the
    operator's reserve switch may be off, the chat may already have banned enough
    accounts to be abandoned, and the account may never have entered the target at all
    — and in every one of those cases the account is still finished. Left to
    :func:`substitute_banned_account`, the row stayed ``active``, ``_load_context``
    dealt the account lines on the next run and the roster card showed it healthy.
    """
    await asyncio.to_thread(_ban_campaign_account, campaign_id, account_id)


def _take_reserve(connection: Connection, campaign_id: str) -> str | None:
    """The oldest unspent reserve account of this campaign."""
    statement = (
        select(_neuroshilling_accounts.c.account_id)
        .where(
            (_neuroshilling_accounts.c.campaign_id == campaign_id)
            & (_neuroshilling_accounts.c.is_reserve == 1)
            & (_neuroshilling_accounts.c.state == "active"),
        )
        .order_by(
            _neuroshilling_accounts.c.created_at.asc(),
            _neuroshilling_accounts.c.account_id.asc(),
        )
        .limit(1)
    )
    taken = connection.execute(statement).scalar_one_or_none()
    return None if taken is None else str(taken)


class ReserveSwap(NamedTuple):
    """What one substitution attempt did: who claimed the ban, and who took the role.

    Two booleans' worth of outcome in one value, because the caller says a different
    thing to the operator for each: ``claimed=False`` means another caller had already
    spent this ban's one substitution, while ``claimed=True`` with ``stand_in=None``
    means the pool is empty. Collapsing both into a bare ``None`` told the operator the
    pool was empty when it was full.
    """

    claimed: bool
    stand_in: str | None


def _substitute_banned_account(campaign_id: str, account_id: str) -> ReserveSwap:
    with _get_engine().begin() as connection:
        _record_ban(connection, campaign_id, account_id)
        reserve = _take_reserve(connection, campaign_id)
        if reserve is None:
            return ReserveSwap(claimed=True, stand_in=None)
        if not _claim_replacement(connection, campaign_id, account_id, reserve):
            return ReserveSwap(claimed=False, stand_in=None)
        role_id = connection.execute(
            select(_neuroshilling_accounts.c.role_id).where(_at(campaign_id, account_id)),
        ).scalar_one()
        # The reserve row is UPDATED into the role, never inserted: ``(campaign_id,
        # account_id)`` is the primary key and the reserve already holds its half of it.
        connection.execute(
            update(_neuroshilling_accounts)
            .where(_at(campaign_id, reserve))
            .values(is_reserve=0, role_id=role_id),
        )
        return ReserveSwap(claimed=True, stand_in=reserve)


async def substitute_banned_account(campaign_id: str, account_id: str) -> ReserveSwap:
    """Promote one reserve into ``account_id``'s role, and re-assert its ban.

    The ban is re-written here as well as by :func:`ban_campaign_account` because it
    costs one idempotent statement and this transaction must not be able to promote a
    reserve into the role of an account the roster still calls ``active``.

    ``state='banned'`` is PERMANENT. Nothing in this project ever writes it back to
    ``active``: ``engine._load_context`` skips the row on every future run of the
    campaign, and ``retire_account_presence`` has retired the account across its
    presence rows besides. That is deliberately unlike ``flooded``, which
    ``_telegram.flood_since`` ages out of its own accord — but it is worth knowing
    that the trigger, ``UserBannedInChannelError``, is the account-wide anti-spam
    restriction ``services.neurocomment.bans`` documents, and Telegram routinely
    lifts one after 24h-7d. The operator's way back is the roster card: the state is
    carried forward only for accounts that are still rostered
    (:func:`_replace_campaign_accounts` re-reads it before the delete), so removing
    the account, saving, adding it again and saving a second time gives it a fresh
    ``active`` row.
    """
    return await asyncio.to_thread(_substitute_banned_account, campaign_id, account_id)


def _count_substitutions(campaign_id: str) -> int:
    statement = select(func.count()).where(
        (_neuroshilling_accounts.c.campaign_id == campaign_id)
        & (_neuroshilling_accounts.c.replaced_by_account_id.is_not(None)),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_substitutions(campaign_id: str) -> int:
    """How many of this campaign's accounts have been replaced from the reserve pool.

    Counted off ``replaced_by_account_id`` rather than off ``state='banned'``: a ban
    with an empty pool writes the state and no replacement, and the launch card's
    number is about the replacements that happened.
    """
    return await asyncio.to_thread(_count_substitutions, campaign_id)


def _list_campaign_role_ids(campaign_id: str) -> set[str]:
    statement = select(_neuroshilling_roles.c.role_id).where(
        _neuroshilling_roles.c.campaign_id == campaign_id,
    )
    with _get_engine().connect() as connection:
        return {str(role_id) for (role_id,) in connection.execute(statement)}


async def list_campaign_role_ids(campaign_id: str) -> set[str]:
    """Every role id this campaign owns — the only ones its roster may name.

    Lives beside the roster rather than with the campaign because that is the one
    thing it is for: ``neuroshilling_accounts.role_id`` is a real foreign key, so
    a roster entry naming another campaign's role is an ``IntegrityError`` unless
    somebody checks first.
    """
    return await asyncio.to_thread(_list_campaign_role_ids, campaign_id)
