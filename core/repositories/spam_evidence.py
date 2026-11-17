"""Spam evidence: what was observed about an account, coalesced and bounded.

Sync helpers on a worker thread, the ``spam_probe_ledger`` shape. Only ``services.spam_intel``
may import this module (``tests/test_architecture.py``): the rest of the code records evidence
through ``services.spam_intel.record_evidence`` and reads verdicts through ``verdicts_for``.

* **Coalescing.** A new item for the same account, kind and source within ``window`` of the
  newest such row adds to its ``count`` and moves ``last_seen_at`` instead of adding a row. A
  STATE kind (``freeze_seen`` and its closer, ``tos_restricted`` and its closer) coalesces
  across any span for as long as its opposite has not come in since: a frozen account that is
  checked every cycle is one row, not a row per cycle.
* **Retention.** :func:`prune_evidence` drops rows older than a cut-off and keeps the newest
  ``max_per_account`` per account. The newest row of each state kind is never dropped, or an
  old freeze would stop being one.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, delete, func, select, update

from core._schema_spam_evidence import _account_spam_evidence
from core.db import _get_engine
from schemas.spam_evidence import STATE_KIND_CLOSER, Evidence

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import Row, Select
    from sqlalchemy.engine import Connection

_T = _account_spam_evidence
_STATE_KINDS = tuple(STATE_KIND_CLOSER)


def _iso(moment: datetime) -> str:
    """Fixed-width UTC text, so string order is time order."""
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _moment(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _evidence_from_row(mapping: Mapping[str, Any]) -> Evidence:
    values = dict(mapping)
    values.pop("id", None)
    return Evidence.model_validate(values)


def _values(item: Evidence) -> dict[str, Any]:
    return {
        "account_id": item.account_id,
        "kind": item.kind,
        "direction": item.direction,
        "observed_at": _iso(item.observed_at),
        "expires_at": None if item.expires_at is None else _iso(item.expires_at),
        "source": item.source,
        "operation": item.operation,
        "detail": item.detail,
        "count": item.count,
        "last_seen_at": _iso(item.last_seen_at or item.observed_at),
    }


def _coalesces(connection: Connection, item: Evidence, newest: Row[Any], window: timedelta) -> bool:
    newest_at = _moment(newest.observed_at)
    closer = STATE_KIND_CLOSER.get(item.kind)
    if closer is None:
        return abs(item.observed_at - newest_at) < window
    # A state kind: the same state is still on while its opposite has not come in since.
    opposite = connection.execute(
        select(func.count()).where(
            _T.c.account_id == item.account_id,
            _T.c.kind == closer,
            _T.c.observed_at > newest.observed_at,
        )
    ).scalar()
    return not opposite


def _record_evidence(item: Evidence, window: timedelta) -> bool:
    with _get_engine().begin() as connection:
        newest = connection.execute(
            select(_T.c.id, _T.c.observed_at, _T.c.last_seen_at, _T.c.count.label("seen"))
            .where(
                _T.c.account_id == item.account_id,
                _T.c.kind == item.kind,
                _T.c.source == item.source,
            )
            .order_by(_T.c.observed_at.desc(), _T.c.id.desc())
            .limit(1)
        ).first()
        if newest is not None and _coalesces(connection, item, newest, window):
            seen = max(_moment(newest.last_seen_at), item.last_seen_at or item.observed_at)
            connection.execute(
                update(_T)
                .where(_T.c.id == newest.id)
                .values(
                    count=newest.seen + item.count,
                    last_seen_at=_iso(seen),
                    # The latest sighting's own facts replace the row's: a later date, a
                    # fresher reason. ``observed_at`` stays the first sighting.
                    expires_at=None if item.expires_at is None else _iso(item.expires_at),
                    operation=item.operation,
                    detail=item.detail,
                )
            )
            return False
        connection.execute(_T.insert().values(**_values(item)))
    return True


async def record_evidence(item: Evidence, *, window: timedelta) -> bool:
    """Store one observation; ``True`` for a new row, ``False`` when it joined an older one."""
    return await asyncio.to_thread(_record_evidence, item, window)


def _list_evidence(account_id: str, limit: int | None) -> list[Evidence]:
    statement = (
        select(_T)
        .where(_T.c.account_id == account_id)
        .order_by(_T.c.observed_at.desc(), _T.c.id.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [_evidence_from_row(cast("Mapping[str, Any]", row)) for row in rows]


async def list_evidence(account_id: str, *, limit: int | None = None) -> list[Evidence]:
    """The account's evidence, newest first."""
    return await asyncio.to_thread(_list_evidence, account_id, limit)


def _protected_ids() -> Select[tuple[int]]:
    """The newest row of each state kind per account (never pruned)."""
    return (
        select(func.max(_T.c.id))
        .where(_T.c.kind.in_(_STATE_KINDS))
        .group_by(_T.c.account_id, _T.c.kind)
    )


def _prune_evidence(before: datetime, max_per_account: int, account_id: str | None) -> int:
    unprotected = _T.c.id.not_in(_protected_ids())
    scope = unprotected if account_id is None else and_(unprotected, _T.c.account_id == account_id)
    with _get_engine().begin() as connection:
        removed = connection.execute(
            delete(_T).where(_T.c.last_seen_at < _iso(before), unprotected)
        ).rowcount
        ranked = (
            select(
                _T.c.id,
                func.row_number()
                .over(
                    partition_by=_T.c.account_id,
                    order_by=(_T.c.observed_at.desc(), _T.c.id.desc()),
                )
                .label("rank"),
            )
            .where(scope)
            .subquery()
        )
        excess = select(ranked.c.id).where(ranked.c.rank > max_per_account)
        removed += connection.execute(delete(_T).where(_T.c.id.in_(excess))).rowcount
    return removed


async def prune_evidence(
    before: datetime, max_per_account: int, *, account_id: str | None = None
) -> int:
    """Drop evidence last seen before ``before``, then all but the newest ``max_per_account``.

    ``account_id`` limits the per-account cap to one account (the age cut-off is fleet-wide:
    it is one indexed range delete). The newest row of each state kind is always kept.
    """
    return await asyncio.to_thread(_prune_evidence, before, max_per_account, account_id)


def _delete_evidence(account_id: str) -> int:
    with _get_engine().begin() as connection:
        return connection.execute(delete(_T).where(_T.c.account_id == account_id)).rowcount


async def delete_evidence(account_id: str) -> int:
    """Remove every evidence row of an account."""
    return await asyncio.to_thread(_delete_evidence, account_id)


def delete_account_evidence(connection: Connection, account_id: str) -> None:
    """The account's evidence goes with it (called inside the account delete)."""
    connection.execute(delete(_T).where(_T.c.account_id == account_id))


__all__ = [
    "delete_account_evidence",
    "delete_evidence",
    "list_evidence",
    "prune_evidence",
    "record_evidence",
]
