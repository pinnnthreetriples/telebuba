"""Atomic campaign form and scenario replacement for one settings Save."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._accounts import _replace_campaign_accounts
from core.repositories.neuroshilling._campaigns import (
    _EDITABLE_COLUMNS,
    _newer_stamp,
    _row_to_campaign,
)
from core.repositories.neuroshilling._scenario import (
    _select_roles,
    _select_steps,
    _set_scenario_status,
    _write_roles,
    _write_steps,
)
from core.repositories.neuroshilling._tables import (
    _neuroshilling_accounts,
    _neuroshilling_campaigns,
)
from schemas.neuroshilling import NeuroshillingAccountAssignment
from schemas.neuroshilling_scenario import NeuroshillingScenario, NeuroshillingSettings

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from schemas.neuroshilling_scenario import NeuroshillingSettingsUpdate


def _select_assignments(
    connection: Connection, campaign_id: str
) -> list[NeuroshillingAccountAssignment]:
    rows = connection.execute(
        select(
            _neuroshilling_accounts.c.account_id,
            _neuroshilling_accounts.c.role_id,
            _neuroshilling_accounts.c.is_reserve,
        )
        .where(_neuroshilling_accounts.c.campaign_id == campaign_id)
        .order_by(
            _neuroshilling_accounts.c.created_at.asc(),
            _neuroshilling_accounts.c.account_id.asc(),
        )
    ).mappings()
    return [NeuroshillingAccountAssignment.model_validate(dict(row)) for row in rows]


def _load_settings(campaign_id: str) -> NeuroshillingSettings | None:
    with _get_engine().begin() as connection:
        # SQLite's legacy transaction mode does not BEGIN for a SELECT on its own.
        connection.exec_driver_sql("BEGIN")
        row = (
            connection.execute(
                select(_neuroshilling_campaigns).where(
                    _neuroshilling_campaigns.c.campaign_id == campaign_id
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return NeuroshillingSettings(
            campaign=_row_to_campaign(row),
            accounts=_select_assignments(connection, campaign_id),
            scenario=NeuroshillingScenario(
                campaign_id=campaign_id,
                scenario_status=row["scenario_status"],
                roles=_select_roles(connection, campaign_id),
                steps=_select_steps(connection, campaign_id),
            ),
        )


async def load_settings(campaign_id: str) -> NeuroshillingSettings | None:
    return await asyncio.to_thread(_load_settings, campaign_id)


def _save_settings(
    campaign_id: str, data: NeuroshillingSettingsUpdate
) -> NeuroshillingSettings | None:
    values: dict[str, object] = {name: getattr(data.campaign, name) for name in _EDITABLE_COLUMNS}
    values["updated_at"] = _newer_stamp(data.campaign.expected_updated_at)
    with _get_engine().begin() as connection:
        changed = connection.execute(
            update(_neuroshilling_campaigns)
            .where(
                _neuroshilling_campaigns.c.campaign_id == campaign_id,
                _neuroshilling_campaigns.c.updated_at == data.campaign.expected_updated_at,
            )
            .values(**values)
        )
        if changed.rowcount == 0:
            return None
        now = _now_iso()
        role_ids = _write_roles(connection, campaign_id, data.scenario.roles, now)
        _write_steps(connection, campaign_id, data.scenario.steps, role_ids, now)
        assignments = [
            item.model_copy(
                update={"role_id": role_ids.get(item.role_id) if item.role_id is not None else None}
            )
            for item in data.campaign.accounts
        ]
        _replace_campaign_accounts(connection, campaign_id, assignments)
        _set_scenario_status(connection, campaign_id, "draft")
        row = (
            connection.execute(
                select(_neuroshilling_campaigns).where(
                    _neuroshilling_campaigns.c.campaign_id == campaign_id
                )
            )
            .mappings()
            .one()
        )
        return NeuroshillingSettings(
            campaign=_row_to_campaign(row),
            accounts=_select_assignments(connection, campaign_id),
            scenario=NeuroshillingScenario(
                campaign_id=campaign_id,
                scenario_status="draft",
                roles=_select_roles(connection, campaign_id),
                steps=_select_steps(connection, campaign_id),
            ),
        )


async def save_settings(
    campaign_id: str, data: NeuroshillingSettingsUpdate
) -> NeuroshillingSettings | None:
    return await asyncio.to_thread(_save_settings, campaign_id, data)
