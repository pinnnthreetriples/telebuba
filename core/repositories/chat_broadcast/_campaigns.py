"""Campaign rows: create, read, whole-dialog save, run-state transitions, delete."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, func, insert, select, update

from core.db import _get_engine, _now_iso
from core.repositories.chat_broadcast._tables import (
    _chat_broadcast_accounts,
    _chat_broadcast_campaigns,
    _chat_broadcast_targets,
)
from schemas.chat_broadcast_records import CampaignRecord

if TYPE_CHECKING:
    from sqlalchemy import Connection, RowMapping, Select

    from schemas.chat_broadcast import ChatBroadcastStatus

_LIVE_STATUSES = ("running", "stopping")
# Runtime columns the engine moves while it works; they never touch the edit token,
# because the settings dialog cannot save while a run is attached anyway.
_RUNTIME_COLUMNS = frozenset(
    {"round", "rest_until_unix", "started_unix", "resumed_unix", "finished_unix", "last_error"}
)


def _newer_stamp(previous: str) -> str:
    """Keep the edit token increasing even if two writes share a clock tick."""
    now = datetime.fromisoformat(_now_iso())
    return max(now, datetime.fromisoformat(previous) + timedelta(microseconds=1)).isoformat()


def _select_campaigns() -> Select:
    accounts = (
        select(func.count())
        .where(_chat_broadcast_accounts.c.campaign_id == _chat_broadcast_campaigns.c.campaign_id)
        .scalar_subquery()
    )
    targets = (
        select(func.count())
        .where(_chat_broadcast_targets.c.campaign_id == _chat_broadcast_campaigns.c.campaign_id)
        .scalar_subquery()
    )
    return select(
        _chat_broadcast_campaigns,
        accounts.label("account_count"),
        targets.label("target_count"),
    )


def _record(row: RowMapping) -> CampaignRecord:
    return CampaignRecord.model_validate(dict(row))


def _create_campaign(name: str) -> CampaignRecord:
    now = _now_iso()
    campaign_id = uuid4().hex
    with _get_engine().begin() as connection:
        connection.execute(
            insert(_chat_broadcast_campaigns).values(
                campaign_id=campaign_id, name=name, created_at=now, updated_at=now
            ),
        )
    created = _fetch_campaign(campaign_id)
    if created is None:  # pragma: no cover - the insert above guarantees the row
        msg = f"Campaign was not persisted: {campaign_id}"
        raise RuntimeError(msg)
    return created


async def create_campaign(name: str) -> CampaignRecord:
    return await asyncio.to_thread(_create_campaign, name)


def _fetch_campaign(campaign_id: str) -> CampaignRecord | None:
    statement = _select_campaigns().where(_chat_broadcast_campaigns.c.campaign_id == campaign_id)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _record(row)


async def fetch_campaign(campaign_id: str) -> CampaignRecord | None:
    return await asyncio.to_thread(_fetch_campaign, campaign_id)


def _list_campaigns(statuses: tuple[str, ...] | None) -> list[CampaignRecord]:
    statement = _select_campaigns().order_by(_chat_broadcast_campaigns.c.created_at.asc())
    if statuses is not None:
        statement = statement.where(_chat_broadcast_campaigns.c.status.in_(statuses))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_record(row) for row in rows]


async def list_campaigns() -> list[CampaignRecord]:
    return await asyncio.to_thread(_list_campaigns, None)


async def list_live_campaigns() -> list[CampaignRecord]:
    """Campaigns a run is attached to — what a restart has to resume or settle."""
    return await asyncio.to_thread(_list_campaigns, _LIVE_STATUSES)


def _list_running_account_names() -> dict[str, tuple[str, str]]:
    statement = (
        select(
            _chat_broadcast_accounts.c.account_id,
            _chat_broadcast_campaigns.c.campaign_id,
            _chat_broadcast_campaigns.c.name,
        )
        .select_from(
            _chat_broadcast_accounts.join(
                _chat_broadcast_campaigns,
                _chat_broadcast_accounts.c.campaign_id == _chat_broadcast_campaigns.c.campaign_id,
            ),
        )
        .where(_chat_broadcast_campaigns.c.status.in_(_LIVE_STATUSES))
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return {str(row[0]): (str(row[1]), str(row[2])) for row in rows}


async def list_running_account_names() -> dict[str, tuple[str, str]]:
    """``account_id -> (campaign_id, name)`` for rosters of running campaigns."""
    return await asyncio.to_thread(_list_running_account_names)


def _replace_roster(connection: Connection, campaign_id: str, account_ids: list[str]) -> None:
    """Keep each kept account's runtime state; drop the removed; append the new."""
    table = _chat_broadcast_accounts
    kept = set(account_ids)
    connection.execute(
        delete(table).where(table.c.campaign_id == campaign_id, table.c.account_id.not_in(kept)),
    )
    existing = set(
        connection.execute(
            select(table.c.account_id).where(table.c.campaign_id == campaign_id)
        ).scalars()
    )
    for position, account_id in enumerate(account_ids):
        if account_id in existing:
            connection.execute(
                update(table)
                .where(table.c.campaign_id == campaign_id, table.c.account_id == account_id)
                .values(position=position),
            )
        else:
            connection.execute(
                insert(table).values(
                    campaign_id=campaign_id, account_id=account_id, position=position
                ),
            )


def _save_settings(
    campaign_id: str,
    *,
    name: str,
    settings_json: str,
    account_ids: list[str],
    expected_updated_at: str,
) -> CampaignRecord | None:
    table = _chat_broadcast_campaigns
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        current = connection.execute(
            select(table.c.updated_at, table.c.status).where(table.c.campaign_id == campaign_id)
        ).first()
        if (
            current is None
            or current[0] != expected_updated_at
            or str(current[1]) in _LIVE_STATUSES
        ):
            return None
        connection.execute(
            update(table)
            .where(table.c.campaign_id == campaign_id)
            .values(
                name=name, settings_json=settings_json, updated_at=_newer_stamp(str(current[0]))
            ),
        )
        _replace_roster(connection, campaign_id, account_ids)
    return _fetch_campaign(campaign_id)


async def save_settings(
    campaign_id: str,
    *,
    name: str,
    settings_json: str,
    account_ids: list[str],
    expected_updated_at: str,
) -> CampaignRecord | None:
    """Write name, settings and roster in one transaction, only on an unchanged stamp.

    ``None`` when the row is gone, its stamp moved, or a run is attached — the service
    tells the three apart with a fresh read.
    """
    return await asyncio.to_thread(
        lambda: _save_settings(
            campaign_id,
            name=name,
            settings_json=settings_json,
            account_ids=account_ids,
            expected_updated_at=expected_updated_at,
        ),
    )


def _save_pace(campaign_id: str, settings_json: str, expected_updated_at: str) -> bool:
    table = _chat_broadcast_campaigns
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        current = connection.execute(
            select(table.c.updated_at).where(table.c.campaign_id == campaign_id)
        ).scalar_one_or_none()
        if current is None or current != expected_updated_at:
            return False
        connection.execute(
            update(table)
            .where(table.c.campaign_id == campaign_id)
            .values(settings_json=settings_json, updated_at=_newer_stamp(str(current))),
        )
    return True


async def save_pace(campaign_id: str, *, settings_json: str, expected_updated_at: str) -> bool:
    """Write settings whose only change is the pauses — allowed while a run is attached.

    Bumps the edit token, so a dialog opened before it cannot save over the new pauses.
    ``False`` when the row is gone or its stamp moved since the caller read it.
    """
    return await asyncio.to_thread(_save_pace, campaign_id, settings_json, expected_updated_at)


def _move_rest(campaign_id: str, round_number: int, until: int) -> bool:
    table = _chat_broadcast_campaigns
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(table)
            .where(
                table.c.campaign_id == campaign_id,
                table.c.round == round_number,
                table.c.rest_until_unix.is_not(None),
            )
            .values(rest_until_unix=until),
        )
    return result.rowcount > 0


async def move_rest(campaign_id: str, *, round_number: int, until: int) -> bool:
    """Move the end of the rest after ``round_number`` — only while that rest still runs.

    The engine clears the rest as it opens the next round; a move that lands after that
    must not put a rest into the middle of the new round.
    """
    return await asyncio.to_thread(_move_rest, campaign_id, round_number, until)


def _set_status(
    campaign_id: str,
    status: ChatBroadcastStatus,
    fields: dict[str, object],
    expected_updated_at: str | None,
) -> bool:
    table = _chat_broadcast_campaigns
    with _get_engine().begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        current = connection.execute(
            select(table.c.updated_at).where(table.c.campaign_id == campaign_id)
        ).scalar_one_or_none()
        if current is None or (expected_updated_at is not None and current != expected_updated_at):
            return False
        connection.execute(
            update(table)
            .where(table.c.campaign_id == campaign_id)
            .values(status=status, updated_at=_newer_stamp(str(current)), **fields),
        )
    return True


async def set_status(
    campaign_id: str,
    status: ChatBroadcastStatus,
    *,
    expected_updated_at: str | None = None,
    **fields: object,
) -> bool:
    """Move the run state (start, stop, settle); bumps the edit token."""
    unknown = set(fields) - _RUNTIME_COLUMNS - {"run_id"}
    if unknown:
        msg = f"Not a run-state column: {sorted(unknown)}"
        raise ValueError(msg)
    return await asyncio.to_thread(_set_status, campaign_id, status, fields, expected_updated_at)


def _set_runtime(campaign_id: str, fields: dict[str, object]) -> None:
    table = _chat_broadcast_campaigns
    with _get_engine().begin() as connection:
        connection.execute(update(table).where(table.c.campaign_id == campaign_id).values(**fields))


async def set_runtime(campaign_id: str, **fields: object) -> None:
    """Move the round / rest window while a run works; the edit token stays."""
    unknown = set(fields) - _RUNTIME_COLUMNS
    if unknown:
        msg = f"Not a runtime column: {sorted(unknown)}"
        raise ValueError(msg)
    await asyncio.to_thread(_set_runtime, campaign_id, fields)


def _delete_campaign(campaign_id: str) -> bool:
    table = _chat_broadcast_campaigns
    with _get_engine().begin() as connection:
        result = connection.execute(delete(table).where(table.c.campaign_id == campaign_id))
    return result.rowcount > 0


async def delete_campaign(campaign_id: str) -> bool:
    """Delete the campaign; its roster, chats, journal and history cascade."""
    return await asyncio.to_thread(_delete_campaign, campaign_id)
