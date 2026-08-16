"""Campaign-side neuroshilling queries: create, read, whole-form update, delete."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from core.db import _get_engine, _now_iso
from core.repositories.neuroshilling._accounts import _replace_campaign_accounts
from core.repositories.neuroshilling._tables import (
    _neuroshilling_accounts,
    _neuroshilling_campaigns,
    _neuroshilling_messages,
    _neuroshilling_presence,
    _neuroshilling_roles,
    _neuroshilling_steps,
)
from schemas.neuroshilling import NeuroshillingCampaign, NeuroshillingCampaignList

if TYPE_CHECKING:
    from sqlalchemy import RowMapping

    from schemas.neuroshilling import (
        NeuroshillingCampaignCreate,
        NeuroshillingCampaignUpdate,
    )

# Editable through the whole-form update, in column order. Held as a tuple so the
# update statement and the request model cannot drift apart silently: every name
# here must exist on ``NeuroshillingCampaignUpdate``.
# The two statuses that mean "a run is attached to this row". Named once because three
# queries key off it and a fourth spelling would drift.
_LIVE_STATUSES = ("running", "stopping")

_EDITABLE_COLUMNS = (
    "name",
    "mode",
    "topic",
    "targets_raw",
    "unique_messages",
    "use_chat_context",
    "media_message_link",
    "media_step_position",
    "run_mode",
    "pause_min_seconds",
    "pause_max_seconds",
    "messages_per_hour",
    "messages_per_chat_per_day",
    "total_per_account",
    "reserve_enabled",
    "autoresponder",
    "reply_to_humans",
    "reply_activity",
    "listen_minutes",
)


def _row_to_campaign(row: RowMapping) -> NeuroshillingCampaign:
    return NeuroshillingCampaign.model_validate(dict(row))


def _create_campaign(data: NeuroshillingCampaignCreate) -> NeuroshillingCampaign:
    now = _now_iso()
    campaign_id = uuid4().hex
    with _get_engine().begin() as connection:
        connection.execute(
            insert(_neuroshilling_campaigns).values(
                campaign_id=campaign_id,
                name=data.name,
                mode=data.mode,
                created_at=now,
                updated_at=now,
            ),
        )
    campaign = _fetch_campaign(campaign_id)
    if campaign is None:  # pragma: no cover - the insert above guarantees the row
        msg = f"Campaign was not persisted: {campaign_id}"
        raise RuntimeError(msg)
    return campaign


async def create_campaign(data: NeuroshillingCampaignCreate) -> NeuroshillingCampaign:
    """Open a campaign with a generated id; every other column takes its default."""
    return await asyncio.to_thread(_create_campaign, data)


def _fetch_campaign(campaign_id: str) -> NeuroshillingCampaign | None:
    statement = select(_neuroshilling_campaigns).where(
        _neuroshilling_campaigns.c.campaign_id == campaign_id,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else _row_to_campaign(row)


async def fetch_campaign(campaign_id: str) -> NeuroshillingCampaign | None:
    return await asyncio.to_thread(_fetch_campaign, campaign_id)


def _list_campaigns() -> NeuroshillingCampaignList:
    statement = select(_neuroshilling_campaigns).order_by(
        _neuroshilling_campaigns.c.created_at.asc(),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return NeuroshillingCampaignList(campaigns=[_row_to_campaign(row) for row in rows])


async def list_campaigns() -> NeuroshillingCampaignList:
    return await asyncio.to_thread(_list_campaigns)


def _list_running_campaign_account_names() -> dict[str, tuple[str, str]]:
    statement = (
        select(
            _neuroshilling_accounts.c.account_id,
            _neuroshilling_campaigns.c.campaign_id,
            _neuroshilling_campaigns.c.name,
        )
        .select_from(
            _neuroshilling_accounts.join(
                _neuroshilling_campaigns,
                _neuroshilling_accounts.c.campaign_id == _neuroshilling_campaigns.c.campaign_id,
            ),
        )
        .where(_neuroshilling_campaigns.c.status.in_(_LIVE_STATUSES))
    )
    with _get_engine().connect() as connection:
        return {
            str(account_id): (str(campaign_id), str(name))
            for account_id, campaign_id, name in connection.execute(statement)
        }


async def list_running_campaign_account_names() -> dict[str, tuple[str, str]]:
    """``account_id -> (campaign_id, campaign name)`` for every account a live run holds.

    The durable half of "this account is busy neuroshilling". The in-memory ownership
    registry is authoritative while a run is actually in flight, but it is empty
    in a process that has just started and has not reconciled yet, while these
    rows still say ``running`` — so the board consults both.
    """
    return await asyncio.to_thread(_list_running_campaign_account_names)


def _list_live_campaigns() -> list[NeuroshillingCampaign]:
    statement = (
        select(_neuroshilling_campaigns)
        .where(_neuroshilling_campaigns.c.status.in_(_LIVE_STATUSES))
        .order_by(_neuroshilling_campaigns.c.created_at.asc())
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_row_to_campaign(row) for row in rows]


async def list_live_campaigns() -> list[NeuroshillingCampaign]:
    """Campaigns whose row still claims a run — what startup reconciliation works from.

    A row in either state means the previous process was playing this campaign when it
    died: nothing else writes them, and the engine clears them the moment a run reaches
    a terminal state.
    """
    return await asyncio.to_thread(_list_live_campaigns)


def _set_run_state(
    campaign_id: str,
    status: str,
    run_id: str | None,
    last_error: str | None,
) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neuroshilling_campaigns)
            .where(_neuroshilling_campaigns.c.campaign_id == campaign_id)
            .values(
                status=status,
                run_id=run_id,
                last_error=last_error,
                updated_at=_now_iso(),
            ),
        )


async def set_run_state(
    campaign_id: str,
    status: str,
    *,
    run_id: str | None,
    last_error: str | None = None,
) -> None:
    """Write the engine-owned half of the campaign row: status, run id, last error.

    ``run_id`` is written on EVERY call rather than left alone, so the caller has to
    say what happens to it. That is deliberate: a resumed run must carry the STORED id
    forward — a fresh one would face an empty unique index and replay the whole
    dialogue into chats that already have it — and only a terminal state may clear it.

    ``last_error`` is an exception CLASS NAME. The column is served back by
    ``GET /neuroshilling/campaigns``, and a third-party ``str(exc)`` carries proxy
    credentials and session paths.
    """
    await asyncio.to_thread(_set_run_state, campaign_id, status, run_id, last_error)


def _update_campaign(
    campaign_id: str,
    data: NeuroshillingCampaignUpdate,
    *,
    reset_approval: bool,
) -> NeuroshillingCampaign | None:
    values: dict[str, object] = {name: getattr(data, name) for name in _EDITABLE_COLUMNS}
    values["updated_at"] = _now_iso()
    if reset_approval:
        # Not an editable column — the CALLER decides, from which fields moved, and
        # the only value it can ask for is ``draft``. Nothing outside
        # ``_scenario.approve_scenario`` may write ``approved``.
        values["scenario_status"] = "draft"
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_neuroshilling_campaigns)
            .where(_neuroshilling_campaigns.c.campaign_id == campaign_id)
            .values(**values),
        )
        if result.rowcount == 0:
            return None
        _replace_campaign_accounts(connection, campaign_id, data.accounts)
    return _fetch_campaign(campaign_id)


async def update_campaign(
    campaign_id: str,
    data: NeuroshillingCampaignUpdate,
    *,
    reset_approval: bool = False,
) -> NeuroshillingCampaign | None:
    """Apply the whole edited form, roster included, in one transaction.

    ``None`` means no such campaign. ``scenario_status``, ``status``, ``run_id``
    and ``last_error`` are deliberately absent from the editable set: they are
    engine state, and a request body must not be able to declare a draft approved
    or a stopped run alive.

    ``reset_approval`` is the one exception and it is a boolean, not a value: the
    service works out whether the edit changed WHAT gets said, and the write drops
    the campaign back to ``draft`` in the same transaction so no window exists in
    which an approval vouches for a topic that has already changed.
    """
    return await asyncio.to_thread(
        _update_campaign,
        campaign_id,
        data,
        reset_approval=reset_approval,
    )


def _delete_campaign(campaign_id: str) -> None:
    # Children are deleted explicitly, innermost first, rather than left to the
    # ON DELETE CASCADE chain: ``neuroshilling_messages`` references BOTH the
    # campaign (cascading) and a step (not cascading), so the order SQLite happens
    # to unwind the cascade in would decide whether the step rows still have
    # journal rows pointing at them. Explicit order makes that not a question.
    with _get_engine().begin() as connection:
        for table in (
            _neuroshilling_messages,
            _neuroshilling_presence,
            _neuroshilling_steps,
            _neuroshilling_accounts,
            _neuroshilling_roles,
        ):
            connection.execute(delete(table).where(table.c.campaign_id == campaign_id))
        connection.execute(
            delete(_neuroshilling_campaigns).where(
                _neuroshilling_campaigns.c.campaign_id == campaign_id,
            ),
        )


async def delete_campaign(campaign_id: str) -> None:
    """Delete a campaign and everything hanging off it (idempotent)."""
    await asyncio.to_thread(_delete_campaign, campaign_id)
