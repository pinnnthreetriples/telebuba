"""Whom the bulk-message tool wrote to, and which "not found" numbers wait for a re-check.

``recipient_history`` only grows: one row per first message that may have left an
account. ``contact_rechecks`` holds one row per queued number; a lookup run reads
the due ones and settles each number it checked.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_recipients import _contact_rechecks, _recipient_history
from core.db import _get_engine
from schemas.recipient_history import ContactRecheck, RecipientContact

if TYPE_CHECKING:
    from collections.abc import Sequence


def _add_recipient_contact(contact: RecipientContact) -> None:
    with _get_engine().begin() as connection:
        connection.execute(insert(_recipient_history).values(**contact.model_dump()))


async def add_recipient_contact(contact: RecipientContact) -> None:
    """Record one first message that may have reached the recipient."""
    await asyncio.to_thread(_add_recipient_contact, contact)


def _find_recipient_contacts(peers: Sequence[str], phones: Sequence[str]) -> list[RecipientContact]:
    table = _recipient_history
    statement = (
        select(table.c.peer, table.c.phone, table.c.account_id, table.c.sent_at)
        .where(or_(table.c.peer.in_(peers), table.c.phone.in_(phones)))
        .order_by(table.c.sent_at, table.c.id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [RecipientContact.model_validate(dict(row)) for row in rows]


async def find_recipient_contacts(
    peers: Sequence[str], phones: Sequence[str]
) -> list[RecipientContact]:
    """Every stored message to one of ``peers`` or ``phones``, oldest first."""
    return await asyncio.to_thread(_find_recipient_contacts, peers, phones)


def _list_contact_rechecks() -> list[ContactRecheck]:
    statement = select(_contact_rechecks).order_by(
        _contact_rechecks.c.due_at, _contact_rechecks.c.created_at
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [ContactRecheck.model_validate(dict(row)) for row in rows]


async def list_contact_rechecks() -> list[ContactRecheck]:
    """Every queued number, the earliest due first."""
    return await asyncio.to_thread(_list_contact_rechecks)


def _count_contact_rechecks(now: str) -> tuple[int, int]:
    table = _contact_rechecks
    statement = select(func.count(), func.count().filter(table.c.due_at <= now))
    with _get_engine().connect() as connection:
        waiting, due = connection.execute(statement.select_from(table)).one()
    return int(waiting), int(due)


async def count_contact_rechecks(now: str) -> tuple[int, int]:
    """How many numbers are queued, and how many of them are due at ``now``."""
    return await asyncio.to_thread(_count_contact_rechecks, now)


def _queue_contact_recheck(recheck: ContactRecheck) -> None:
    statement = sqlite_insert(_contact_rechecks).values(**recheck.model_dump())
    # A number already queued keeps its first account and due time.
    statement = statement.on_conflict_do_nothing(index_elements=[_contact_rechecks.c.phone_key])
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def queue_contact_recheck(recheck: ContactRecheck) -> None:
    """Queue a number for another account; one already queued stays as it was."""
    await asyncio.to_thread(_queue_contact_recheck, recheck)


def _drop_contact_rechecks(phone_keys: Sequence[str]) -> None:
    statement = delete(_contact_rechecks).where(_contact_rechecks.c.phone_key.in_(phone_keys))
    with _get_engine().begin() as connection:
        connection.execute(statement)


async def drop_contact_rechecks(phone_keys: Sequence[str]) -> None:
    """Forget queued numbers that have their answer."""
    if phone_keys:
        await asyncio.to_thread(_drop_contact_rechecks, phone_keys)
