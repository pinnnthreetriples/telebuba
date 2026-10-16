"""The lead queue — ``lead_queue_items`` and the ``lead_queue_settings`` singleton.

A lead row lives as long as the queue: its unique ``phone_key`` is what keeps a
number from being queued, looked up and messaged twice.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_lead_queue import _lead_queue_items, _lead_queue_settings
from core.db import _get_engine, _now_iso
from schemas.lead_queue import LeadQueueItem, LeadQueueSettings

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from sqlalchemy import Select

    from schemas.lead_queue import LeadStatus

_SETTINGS_ID = 1
_items = _lead_queue_items


def _add_leads(leads: Sequence[LeadQueueItem]) -> int:
    now = _now_iso()
    added = 0
    with _get_engine().begin() as connection:
        for lead in leads:
            statement = (
                sqlite_insert(_items)
                .values(**lead.model_dump(exclude={"id"}), updated_at=now)
                .on_conflict_do_nothing(index_elements=[_items.c.phone_key])
            )
            added += connection.execute(statement).rowcount
    return added


async def add_leads(leads: Sequence[LeadQueueItem]) -> int:
    """Insert the leads whose number is not queued yet; returns how many went in."""
    return await asyncio.to_thread(_add_leads, leads)


def _select(statement: Select[tuple[object, ...]]) -> list[LeadQueueItem]:
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [
        LeadQueueItem.model_validate({k: v for k, v in row.items() if k != "updated_at"})
        for row in rows
    ]


async def list_leads(
    statuses: Collection[LeadStatus] | None = None,
    *,
    limit: int | None = None,
    newest_first: bool = False,
    recheck: bool | None = None,
) -> list[LeadQueueItem]:
    """Leads in queue order (oldest first) unless ``newest_first``.

    ``statuses`` narrows; ``recheck`` keeps only re-checks (True) or only the rest (False).
    """
    statement = select(_items)
    if statuses is not None:
        statement = statement.where(_items.c.status.in_(list(statuses)))
    if recheck is not None:
        column = _items.c.recheck_account_id
        statement = statement.where(column.is_not(None) if recheck else column.is_(None))
    statement = statement.order_by(_items.c.id.desc() if newest_first else _items.c.id)
    if limit is not None:
        statement = statement.limit(limit)
    return await asyncio.to_thread(_select, statement)


async def list_account_history(account_id: str, limit: int) -> list[LeadQueueItem]:
    """The leads ``account_id`` looked up, the latest lookup first."""
    statement = (
        select(_items)
        .where((_items.c.account_id == account_id) & _items.c.looked_up_at.is_not(None))
        .order_by(_items.c.looked_up_at.desc(), _items.c.id.desc())
        .limit(limit)
    )
    return await asyncio.to_thread(_select, statement)


async def list_leads_by_user(user_ids: Collection[int]) -> list[LeadQueueItem]:
    statement = select(_items).where(_items.c.user_id.in_(list(user_ids)))
    return await asyncio.to_thread(_select, statement)


def _count_by_status() -> dict[str, int]:
    statement = select(_items.c.status, func.count()).group_by(_items.c.status)
    with _get_engine().connect() as connection:
        return {str(status): int(count) for status, count in connection.execute(statement).all()}


async def count_leads_by_status() -> dict[str, int]:
    return await asyncio.to_thread(_count_by_status)


def _save_leads(leads: Sequence[LeadQueueItem]) -> None:
    now = _now_iso()
    with _get_engine().begin() as connection:
        for lead in leads:
            connection.execute(
                update(_items)
                .where(_items.c.id == lead.id)
                .values(**lead.model_dump(exclude={"id"}), updated_at=now)
            )


async def save_leads(leads: Sequence[LeadQueueItem]) -> None:
    """Overwrite each lead's row in one transaction."""
    await asyncio.to_thread(_save_leads, leads)


def _claim_leads(lead_ids: Sequence[int], run_id: str) -> set[int]:
    now = _now_iso()
    claimed: set[int] = set()
    with _get_engine().begin() as connection:
        for lead_id in lead_ids:
            result = connection.execute(
                update(_items)
                .where((_items.c.id == lead_id) & (_items.c.status == "found"))
                .values(status="messaging", run_id=run_id, updated_at=now)
            )
            if result.rowcount:
                claimed.add(lead_id)
    return claimed


async def claim_leads(lead_ids: Sequence[int], run_id: str) -> set[int]:
    """Move the leads still "found" to "messaging" in ``run_id``; returns the ids moved.

    Conditional per row, so a lead removed (or changed) since it was read is left out.
    """
    return await asyncio.to_thread(_claim_leads, lead_ids, run_id)


def _delete_lead(lead_id: int, statuses: Collection[str]) -> bool:
    with _get_engine().begin() as connection:
        result = connection.execute(
            delete(_items).where((_items.c.id == lead_id) & _items.c.status.in_(list(statuses)))
        )
    return result.rowcount > 0


async def delete_lead(lead_id: int, statuses: Collection[str]) -> bool:
    """Delete the lead only while its status is one of ``statuses``."""
    return await asyncio.to_thread(_delete_lead, lead_id, statuses)


async def get_lead(lead_id: int) -> LeadQueueItem | None:
    leads = await asyncio.to_thread(_select, select(_items).where(_items.c.id == lead_id))
    return leads[0] if leads else None


def _load_settings() -> LeadQueueSettings:
    statement = select(_lead_queue_settings).where(_lead_queue_settings.c.id == _SETTINGS_ID)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return LeadQueueSettings()
    values = cast("Mapping[str, object]", row)
    return LeadQueueSettings(
        account_ids=json.loads(str(values["account_ids_json"])),
        text=str(values["text"]),
        paused=bool(values["paused"]),
        owner_user_id=cast("str | None", values["owner_user_id"]),
    )


async def load_lead_queue_settings() -> LeadQueueSettings:
    """The saved settings, or a paused, empty queue while none are saved."""
    return await asyncio.to_thread(_load_settings)


def _save_settings(data: LeadQueueSettings) -> LeadQueueSettings:
    values = {
        "account_ids_json": json.dumps(data.account_ids),
        "text": data.text,
        "paused": int(data.paused),
        "owner_user_id": data.owner_user_id,
        "updated_at": _now_iso(),
    }
    table = _lead_queue_settings
    with _get_engine().begin() as connection:
        updated = connection.execute(
            update(table).where(table.c.id == _SETTINGS_ID).values(**values)
        )
        if updated.rowcount == 0:
            connection.execute(insert(table).values(id=_SETTINGS_ID, **values))
    return _load_settings()


async def save_lead_queue_settings(data: LeadQueueSettings) -> LeadQueueSettings:
    return await asyncio.to_thread(_save_settings, data)
