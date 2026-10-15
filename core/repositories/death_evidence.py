"""Reads of the ``logs`` table that date an account's death from what was recorded.

The Telegram gateway logs every action it runs for an account: a success as an INFO
row named ``[domain_]telegram_<action>``, a refusal as an ERROR row named
``[domain_]telegram_<action>_failed`` whose ``extra.error_type`` carries the stable
code (``account_frozen``, ``session_dead``, ``account_deactivated``). The survival
journal asks these two questions when it records a death found later; the rule that
combines them lives in ``services.lifecycle_journal``.
"""

from __future__ import annotations

import asyncio
from typing import NamedTuple

from sqlalchemy import func, select

from core.db import _get_engine, _logs

_TELEGRAM = "%telegram\\_%"
_FAILED = "%telegram\\_%\\_failed"


class FailureRow(NamedTuple):
    """When a refusal was logged, and the event that logged it."""

    created_at: str
    event: str


def _latest_telegram_success_at(account_id: str) -> str | None:
    statement = select(func.max(_logs.c.created_at)).where(
        _logs.c.account_id == account_id,
        _logs.c.status == "success",
        _logs.c.event.like(_TELEGRAM, escape="\\"),
    )
    with _get_engine().connect() as connection:
        value = connection.execute(statement).scalar_one_or_none()
    return None if value is None else str(value)


async def latest_telegram_success_at(account_id: str) -> str | None:
    """The latest logged Telegram action that succeeded for this account, if any."""
    return await asyncio.to_thread(_latest_telegram_success_at, account_id)


def _first_failure_after(
    account_id: str,
    error_types: tuple[str, ...],
    after: str | None,
) -> FailureRow | None:
    error_type = func.json_extract(_logs.c.extra, "$.error_type")
    statement = (
        select(_logs.c.created_at, _logs.c.event)
        .where(
            _logs.c.account_id == account_id,
            _logs.c.status == "error",
            _logs.c.event.like(_FAILED, escape="\\"),
            error_type.in_(error_types),
        )
        .order_by(_logs.c.created_at, _logs.c.id)
        .limit(1)
    )
    if after is not None:
        statement = statement.where(_logs.c.created_at > after)
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    return None if row is None else FailureRow(str(row[0]), str(row[1]))


async def first_failure_after(
    account_id: str,
    error_types: tuple[str, ...],
    after: str | None,
) -> FailureRow | None:
    """The earliest logged Telegram refusal with one of ``error_types`` after ``after``."""
    if not error_types:
        return None
    return await asyncio.to_thread(_first_failure_after, account_id, error_types, after)
