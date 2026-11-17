"""The spam-intel ledger: every real @SpamBot question, and each account's probe state.

Sync helpers on a worker thread, the ``core.repositories.session_audits`` shape. The budget
(``services.spam_intel``) is computed from these rows, never from memory, so it survives a
restart. An attempt is written when the question goes out and finished when it ends; an
attempt a crash left ``started`` still counts toward the caps.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core._schema_spam_intel import _spam_probe_attempts, _spam_probe_state
from core.db import _get_engine
from schemas.spam_intel import BudgetSnapshot, ProbeAttemptFinish, ProbeAttemptStart, ProbeState

if TYPE_CHECKING:
    from sqlalchemy import Row, Select
    from sqlalchemy.engine import Connection

_PROBE = "probe"
_OPERATOR = "operator"


def _state_from_row(row: Row[Any]) -> ProbeState:
    return ProbeState.model_validate(dict(row._mapping))  # noqa: SLF001


def _fetch_probe_state(account_id: str) -> ProbeState | None:
    with _get_engine().connect() as connection:
        row = connection.execute(
            select(_spam_probe_state).where(_spam_probe_state.c.account_id == account_id)
        ).first()
    return None if row is None else _state_from_row(row)


async def fetch_probe_state(account_id: str) -> ProbeState | None:
    """The account's probe state, or ``None`` before its first attempt."""
    return await asyncio.to_thread(_fetch_probe_state, account_id)


def _save_probe_state(state: ProbeState) -> None:
    values = state.model_dump(exclude={"account_id"})
    statement = sqlite_insert(_spam_probe_state).values(account_id=state.account_id, **values)
    with _get_engine().begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[_spam_probe_state.c.account_id], set_=values
            )
        )


async def save_probe_state(state: ProbeState) -> None:
    """Insert the account's probe state, or replace it."""
    await asyncio.to_thread(_save_probe_state, state)


def _start_attempt(attempt: ProbeAttemptStart) -> int:
    with _get_engine().begin() as connection:
        result = connection.execute(_spam_probe_attempts.insert().values(**attempt.model_dump()))
        key = result.inserted_primary_key
    return int(key[0]) if key else 0


async def start_attempt(attempt: ProbeAttemptStart) -> int:
    """Record a question about to go out; returns the attempt id."""
    return await asyncio.to_thread(_start_attempt, attempt)


def _finish_attempt(finish: ProbeAttemptFinish) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_spam_probe_attempts)
            .where(_spam_probe_attempts.c.id == finish.attempt_id)
            .values(
                finished_at=finish.finished_at,
                outcome=finish.outcome,
                error_type=finish.error_type,
                duration_ms=finish.duration_ms,
            )
        )


async def finish_attempt(finish: ProbeAttemptFinish) -> None:
    """Close an attempt with its outcome."""
    await asyncio.to_thread(_finish_attempt, finish)


def _scalar(connection: Connection, statement: Select[Any]) -> str | None:
    value = connection.execute(statement).scalar()
    return None if value is None else str(value)


def _load_budget_snapshot(
    account_id: str,
    proxy_key: str | None,
    since: str,
) -> BudgetSnapshot:
    table = _spam_probe_attempts
    probes = table.c.kind == _PROBE
    window = (probes, table.c.started_at >= since)
    with _get_engine().connect() as connection:
        state_row = connection.execute(
            select(_spam_probe_state).where(_spam_probe_state.c.account_id == account_id)
        ).first()
        last_account = _scalar(
            connection,
            select(func.max(table.c.started_at)).where(probes, table.c.account_id == account_id),
        )
        # Spacing counts a history read as well: it is a conversation with the bot too.
        last_fleet = _scalar(connection, select(func.max(table.c.started_at)))
        last_proxy = (
            _scalar(
                connection,
                select(func.max(table.c.started_at)).where(table.c.proxy_key == proxy_key),
            )
            if proxy_key is not None
            else None
        )
        fleet = connection.execute(
            select(func.count(), func.min(table.c.started_at)).where(*window)
        ).one()
        mine = connection.execute(
            select(func.count(), func.min(table.c.started_at)).where(
                *window, table.c.account_id == account_id
            )
        ).one()
        operator = connection.execute(
            select(func.count()).where(
                *window, table.c.account_id == account_id, table.c.purpose == _OPERATOR
            )
        ).scalar()
    return BudgetSnapshot(
        state=None if state_row is None else _state_from_row(state_row),
        last_attempt_started_at=last_account,
        last_fleet_started_at=last_fleet,
        last_proxy_started_at=last_proxy,
        fleet_count_24h=int(fleet[0]),
        fleet_oldest_24h=None if fleet[1] is None else str(fleet[1]),
        account_count_24h=int(mine[0]),
        account_oldest_24h=None if mine[1] is None else str(mine[1]),
        operator_count_24h=int(operator or 0),
    )


async def load_budget_snapshot(
    account_id: str,
    proxy_key: str | None,
    since: str,
) -> BudgetSnapshot:
    """What the budget reads: this account's state, the latest asks and the 24 h counts.

    ``since`` is the ISO start of the rolling 24 h window.
    """
    return await asyncio.to_thread(_load_budget_snapshot, account_id, proxy_key, since)


def _prune_attempts(before: str, max_rows: int) -> int:
    table = _spam_probe_attempts
    with _get_engine().begin() as connection:
        removed = connection.execute(delete(table).where(table.c.started_at < before)).rowcount
        total = connection.execute(select(func.count()).select_from(table)).scalar() or 0
        if total > max_rows:
            oldest = (
                select(table.c.id).order_by(table.c.started_at, table.c.id).limit(total - max_rows)
            )
            removed += connection.execute(delete(table).where(table.c.id.in_(oldest))).rowcount
    return removed


async def prune_attempts(before: str, max_rows: int) -> int:
    """Drop attempts older than ``before`` (ISO), then the oldest beyond ``max_rows``."""
    return await asyncio.to_thread(_prune_attempts, before, max_rows)


def delete_account_ledger(connection: Connection, account_id: str) -> None:
    """The account's attempts and state go with it (called inside the account delete)."""
    connection.execute(
        delete(_spam_probe_attempts).where(_spam_probe_attempts.c.account_id == account_id)
    )
    connection.execute(
        delete(_spam_probe_state).where(_spam_probe_state.c.account_id == account_id)
    )
