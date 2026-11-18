"""The spam-intel ledger: every real @SpamBot question, and each account's probe state.

Sync helpers on a worker thread, the ``core.repositories.session_audits`` shape. The budget
(``services.spam_intel``) is computed from these rows, never from memory, so it survives a
restart. An attempt is written when the question goes out and finished when it ends; an
attempt a crash left ``started`` still counts toward the caps.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, delete, exists, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import aliased

from core._schema_spam_intel import _spam_probe_attempts, _spam_probe_state
from core.config import settings
from core.db import _get_engine
from schemas.spam_card import ProbeActivity
from schemas.spam_intel import BudgetSnapshot, ProbeAttemptFinish, ProbeAttemptStart, ProbeState

if TYPE_CHECKING:
    from sqlalchemy import Row, Select
    from sqlalchemy.engine import Connection

_PROBE = "probe"
_MINUTES_PER_DAY = 24 * 60
_HOURS_PER_DAY = 24
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
    """Insert the account's probe state, or replace it (every column, the history mark too)."""
    await asyncio.to_thread(_save_probe_state, state)


# The row has two writers that must not overwrite each other: the gateway after a probe (the
# probe fields) and the history sweep (``history_read_at``). Each writes only its own columns.
_HISTORY_COLUMNS = frozenset({"history_read_at"})


def _save_probe_outcome(state: ProbeState) -> None:
    values = state.model_dump(exclude={"account_id", *_HISTORY_COLUMNS})
    statement = sqlite_insert(_spam_probe_state).values(account_id=state.account_id, **values)
    with _get_engine().begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[_spam_probe_state.c.account_id], set_=values
            )
        )


async def save_probe_outcome(state: ProbeState) -> None:
    """Write the probe fields of the account's state; ``history_read_at`` is left as it is."""
    await asyncio.to_thread(_save_probe_outcome, state)


def _mark_history_read(account_id: str, at: str) -> None:
    values = {"history_read_at": at, "updated_at": at}
    statement = sqlite_insert(_spam_probe_state).values(account_id=account_id, **values)
    with _get_engine().begin() as connection:
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[_spam_probe_state.c.account_id], set_=values
            )
        )


async def mark_history_read(account_id: str, at: str) -> None:
    """Record that the account's @SpamBot dialog was read; the probe fields are left as they are."""
    await asyncio.to_thread(_mark_history_read, account_id, at)


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
        newest = connection.execute(
            select(table.c.started_at, table.c.outcome, table.c.error_type)
            .where(probes, table.c.account_id == account_id)
            .order_by(table.c.started_at.desc(), table.c.id.desc())
            .limit(1)
        ).first()
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
        last_attempt_started_at=None if newest is None else str(newest[0]),
        last_attempt_outcome=None if newest is None else newest[1],
        last_attempt_error=None if newest is None else newest[2],
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


def _list_probe_states(account_ids: list[str]) -> dict[str, ProbeState]:
    if not account_ids:
        return {}
    statement = select(_spam_probe_state).where(_spam_probe_state.c.account_id.in_(account_ids))
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return {row._mapping["account_id"]: _state_from_row(row) for row in rows}  # noqa: SLF001


async def list_probe_states(account_ids: list[str]) -> dict[str, ProbeState]:
    """The probe state of each given account that has one (one query)."""
    return await asyncio.to_thread(_list_probe_states, account_ids)


def _probe_activity(account_ids: list[str], since: str) -> dict[str, ProbeActivity]:
    if not account_ids:
        return {}
    table = _spam_probe_attempts
    recent = func.sum(case((table.c.started_at >= since, 1), else_=0))
    statement = (
        select(table.c.account_id, func.max(table.c.started_at), recent)
        .where(table.c.kind == _PROBE, table.c.account_id.in_(account_ids))
        .group_by(table.c.account_id)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return {
        str(account): ProbeActivity(last_started_at=str(last), count_7d=int(count or 0))
        for account, last, count in rows
    }


async def probe_activity(account_ids: list[str], since: str) -> dict[str, ProbeActivity]:
    """Each account's newest real probe and how many started since ``since`` (one query)."""
    return await asyncio.to_thread(_probe_activity, account_ids, since)


def _fleet_use(since: str) -> tuple[int, int]:
    table = _spam_probe_attempts
    window = (table.c.kind == _PROBE, table.c.started_at >= since)
    intel = settings.spam_intel
    # An operator check is OUT OF TURN when the account was probed before it, past the
    # coalescing window but inside ``out_of_turn_hours`` (the card's own rule, per account): the
    # one a click needs a confirmation for. A first check, or one long after the last probe, is
    # an ordinary click and does not count toward the fleet warning.
    prior = aliased(table)
    gap_days = func.julianday(table.c.started_at) - func.julianday(prior.c.started_at)
    after_a_probe = (
        exists()
        .where(
            prior.c.account_id == table.c.account_id,
            prior.c.kind == _PROBE,
            gap_days >= intel.coalesce_minutes / _MINUTES_PER_DAY,
            gap_days < intel.out_of_turn_hours / _HOURS_PER_DAY,
        )
        .correlate(table)
    )
    with _get_engine().connect() as connection:
        total = connection.execute(select(func.count()).where(*window)).scalar()
        out_of_turn = connection.execute(
            select(func.count()).where(*window, table.c.purpose == _OPERATOR, after_a_probe)
        ).scalar()
    return int(total or 0), int(out_of_turn or 0)


async def fleet_probe_use(since: str) -> tuple[int, int]:
    """``(every real probe, the operator's out-of-turn checks)`` started since ``since``."""
    return await asyncio.to_thread(_fleet_use, since)


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
