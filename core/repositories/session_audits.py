"""Session audit persistence: the latest snapshot per account and the history of audits.

Sync helpers on a worker thread, the ``core.repositories._accounts_passkey`` shape. One
audit writes both rows in one transaction, so the history never names an audit the
snapshot does not carry. Both go with the account (``core.repositories._accounts_delete``).
"""

from __future__ import annotations

import asyncio

from pydantic import TypeAdapter
from sqlalchemy import desc, insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_session_audits import _account_session_audit_events, _account_session_audits
from core.db import _get_engine
from schemas.session_audit import AccountSession, AccountSessionAudit, SessionAuditEvent

_SESSIONS = TypeAdapter(list[AccountSession])


def _save_session_audit(audit: AccountSessionAudit, killed_count: int) -> None:
    if audit.checked_at is None:
        msg = "an audit is saved with the time it was read"
        raise ValueError(msg)
    values = {
        "checked_at": audit.checked_at,
        "total": audit.total,
        "foreign_count": audit.foreign_count,
        "ttl_days": audit.ttl_days,
        "sessions": _SESSIONS.dump_json(audit.sessions).decode(),
    }
    statement = sqlite_insert(_account_session_audits).values(account_id=audit.account_id, **values)
    with _get_engine().begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[_account_session_audits.c.account_id], set_=values
            )
        )
        connection.execute(
            insert(_account_session_audit_events).values(
                account_id=audit.account_id,
                checked_at=audit.checked_at,
                total=audit.total,
                foreign_count=audit.foreign_count,
                killed_count=killed_count,
            )
        )


async def save_session_audit(audit: AccountSessionAudit, *, killed_count: int = 0) -> None:
    """Replace the account's snapshot with ``audit`` and append it to the history."""
    await asyncio.to_thread(_save_session_audit, audit, killed_count)


def _fetch_session_audit(account_id: str) -> AccountSessionAudit | None:
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(_account_session_audits).where(
                _account_session_audits.c.account_id == account_id
            ),
        ).first()
    if row is None:
        return None
    mapping = row._mapping  # noqa: SLF001
    return AccountSessionAudit(
        account_id=account_id,
        checked_at=mapping["checked_at"],
        ttl_days=mapping["ttl_days"],
        total=mapping["total"],
        foreign_count=mapping["foreign_count"],
        sessions=_SESSIONS.validate_json(mapping["sessions"]),
    )


async def fetch_session_audit(account_id: str) -> AccountSessionAudit | None:
    """The account's latest audit, or ``None`` when none has run."""
    return await asyncio.to_thread(_fetch_session_audit, account_id)


def _list_foreign_session_counts() -> dict[str, int]:
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(_account_session_audits.c.account_id, _account_session_audits.c.foreign_count)
        ).all()
    return {str(account_id): int(count) for account_id, count in rows}


async def list_foreign_session_counts() -> dict[str, int]:
    """How many foreign sessions the last audit found, per audited account (one query)."""
    return await asyncio.to_thread(_list_foreign_session_counts)


def _fetch_last_session_audit_event(account_id: str) -> SessionAuditEvent | None:
    table = _account_session_audit_events
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(table)
            .where(table.c.account_id == account_id)
            .order_by(desc(table.c.id))
            .limit(1)
        ).first()
    return None if row is None else SessionAuditEvent.model_validate(dict(row._mapping))  # noqa: SLF001


async def fetch_last_session_audit_event(account_id: str) -> SessionAuditEvent | None:
    """The newest history row of the account, or ``None``."""
    return await asyncio.to_thread(_fetch_last_session_audit_event, account_id)


def _list_session_audit_times() -> dict[str, str]:
    with _get_engine().connect() as connection:
        rows = connection.execute(
            select(_account_session_audits.c.account_id, _account_session_audits.c.checked_at)
        ).all()
    return {str(account_id): str(checked_at) for account_id, checked_at in rows}


async def list_session_audit_times() -> dict[str, str]:
    """When each audited account was last audited (ISO), one query."""
    return await asyncio.to_thread(_list_session_audit_times)
