"""Roster side of the neuroshilling repository: which accounts play a campaign.

The roster is replaced wholesale rather than patched one link at a time — the
page edits it as one card, and a per-link API would leave windows where a link
points at a role the same save removed.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, insert, select

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._tables import (
    _neuroshilling_accounts,
    _neuroshilling_roles,
)
from schemas.neuroshilling import NeuroshillingCampaignAccount

if TYPE_CHECKING:
    from collections.abc import Sequence

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
