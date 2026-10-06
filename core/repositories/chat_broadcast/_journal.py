"""The send journal and the per-chat history.

A journal row is written ``pending`` BEFORE the send and keyed on
``(run_id, chat_key, round, step_index)``: an occupied key is a step already played, so a
resumed run skips it instead of writing into the chat twice. A row still ``pending``
when the process died may have reached Telegram, so it settles ``unconfirmed`` and is
never re-sent.
"""

from __future__ import annotations

import asyncio
import time

from sqlalchemy import ColumnElement, and_, func, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine
from core.repositories.chat_broadcast._tables import (
    _chat_broadcast_events,
    _chat_broadcast_messages,
)
from schemas.chat_broadcast_records import (
    ChatBroadcastEventKind,
    ChatBroadcastJournalKind,
    ChatBroadcastJournalStatus,
    EventRecord,
    JournalKey,
    JournalRecord,
)

_MESSAGES = _chat_broadcast_messages
_EVENTS = _chat_broadcast_events
# What counts against an account's hourly/daily limit: anything that may have gone out.
_CHARGED = ("pending", "sent", "unconfirmed", "deleted")
# What "we already wrote there" means for the skip-already-written switch.
_WRITTEN = ("sent", "unconfirmed", "deleted")


def _key_filter(key: JournalKey) -> ColumnElement[bool]:
    return and_(
        _MESSAGES.c.run_id == key.run_id,
        _MESSAGES.c.chat_key == key.chat_key,
        _MESSAGES.c.round == key.round,
        _MESSAGES.c.step_index == key.step_index,
    )


def _claim_message(
    key: JournalKey,
    campaign_id: str,
    account_id: str,
    kind: ChatBroadcastJournalKind,
    peer_id: int | None,
) -> bool:
    statement = (
        sqlite_insert(_MESSAGES)
        .values(
            campaign_id=campaign_id,
            account_id=account_id,
            kind=kind,
            peer_id=peer_id,
            status="pending",
            created_unix=int(time.time()),
            **key.model_dump(),
        )
        .on_conflict_do_nothing(index_elements=["run_id", "chat_key", "round", "step_index"])
    )
    with _get_engine().begin() as connection:
        result = connection.execute(statement)
    return result.rowcount > 0


async def claim_message(
    key: JournalKey,
    *,
    campaign_id: str,
    account_id: str,
    kind: ChatBroadcastJournalKind,
    peer_id: int | None = None,
) -> bool:
    """Reserve one step; ``False`` means it was already played (or is being played)."""
    return await asyncio.to_thread(_claim_message, key, campaign_id, account_id, kind, peer_id)


def _settle_message(key: JournalKey, fields: dict[str, object]) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_MESSAGES)
            .where(_key_filter(key), _MESSAGES.c.status == "pending")
            .values(**fields)
        )


async def settle_message(  # noqa: PLR0913 - one keyword per journal column
    key: JournalKey,
    status: ChatBroadcastJournalStatus,
    *,
    text: str = "",
    rewritten: bool = False,
    tg_message_id: int | None = None,
    error_type: str | None = None,
) -> None:
    fields: dict[str, object] = {
        "status": status,
        "text": text,
        "rewritten": int(rewritten),
        "tg_message_id": tg_message_id,
        "error_type": error_type,
    }
    if status == "sent":
        fields["sent_unix"] = int(time.time())
    await asyncio.to_thread(_settle_message, key, fields)


def _unconfirm_pending(run_id: str) -> int:
    with _get_engine().begin() as connection:
        result = connection.execute(
            update(_MESSAGES)
            .where(_MESSAGES.c.run_id == run_id, _MESSAGES.c.status == "pending")
            .values(status="unconfirmed", error_type="InterruptedRun"),
        )
    return int(result.rowcount)


async def unconfirm_pending(run_id: str) -> int:
    """A dead process's in-flight sends: maybe delivered, so never repeated."""
    return await asyncio.to_thread(_unconfirm_pending, run_id)


def _count_account_sends_since(account_id: str, since_unix: int) -> int:
    statement = select(func.count()).where(
        _MESSAGES.c.account_id == account_id,
        _MESSAGES.c.created_unix >= since_unix,
        _MESSAGES.c.status.in_(_CHARGED),
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_account_sends_since(account_id: str, since_unix: int) -> int:
    """Sends of one account across every broadcast campaign since ``since_unix``."""
    return await asyncio.to_thread(_count_account_sends_since, account_id, since_unix)


def _count_run_sent(run_id: str) -> int:
    statement = select(func.count()).where(
        _MESSAGES.c.run_id == run_id, _MESSAGES.c.status.in_(_CHARGED)
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_run_sent(run_id: str) -> int:
    return await asyncio.to_thread(_count_run_sent, run_id)


def _written_before(chat_key: str, peer_id: int | None, run_id: str) -> bool:
    match = _MESSAGES.c.chat_key == chat_key
    if peer_id is not None:
        match = or_(match, _MESSAGES.c.peer_id == peer_id)
    statement = select(func.count()).where(
        match, _MESSAGES.c.run_id != run_id, _MESSAGES.c.status.in_(_WRITTEN)
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one()) > 0


async def written_before(chat_key: str, peer_id: int | None, *, run_id: str) -> bool:
    """Did ANY campaign's earlier run already write into this chat?"""
    return await asyncio.to_thread(_written_before, chat_key, peer_id, run_id)


def _list_journal(campaign_id: str, run_id: str | None) -> list[JournalRecord]:
    statement = select(_MESSAGES).where(_MESSAGES.c.campaign_id == campaign_id)
    if run_id is not None:
        statement = statement.where(_MESSAGES.c.run_id == run_id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement.order_by(_MESSAGES.c.id)).mappings().all()
    return [JournalRecord.model_validate(dict(row)) for row in rows]


async def list_journal(campaign_id: str, run_id: str | None = None) -> list[JournalRecord]:
    return await asyncio.to_thread(_list_journal, campaign_id, run_id)


def _mark_deleted(ids: list[int]) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_MESSAGES)
            .where(_MESSAGES.c.id.in_(ids), _MESSAGES.c.status == "sent")
            .values(status="deleted"),
        )


async def mark_deleted(ids: list[int]) -> None:
    """Our messages a chat admin removed."""
    if ids:
        await asyncio.to_thread(_mark_deleted, ids)


def _add_event(campaign_id: str, event: EventRecord) -> None:
    with _get_engine().begin() as connection:
        connection.execute(insert(_EVENTS).values(campaign_id=campaign_id, **event.model_dump()))


async def add_event(  # noqa: PLR0913 - one keyword per history column
    campaign_id: str,
    chat_key: str,
    *,
    round_number: int,
    kind: ChatBroadcastEventKind,
    account_id: str | None = None,
    detail: str | None = None,
) -> None:
    event = EventRecord(
        chat_key=chat_key,
        round=round_number,
        account_id=account_id,
        kind=kind,
        detail=detail,
        at_unix=int(time.time()),
    )
    await asyncio.to_thread(_add_event, campaign_id, event)


def _list_events(campaign_id: str) -> list[EventRecord]:
    statement = select(_EVENTS).where(_EVENTS.c.campaign_id == campaign_id).order_by(_EVENTS.c.id)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [EventRecord.model_validate(dict(row)) for row in rows]


async def list_events(campaign_id: str) -> list[EventRecord]:
    return await asyncio.to_thread(_list_events, campaign_id)
