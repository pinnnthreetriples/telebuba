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
  old freeze would stop being one, and neither is the newest @SpamBot limit that has not ended:
  it is what decides the account. A SHADOW row (a success that proves nothing yet) has its own
  small quota, so a busy account's shadow sightings can never crowd real evidence out of the cap.
* **Counting.** A repeat adds to ``count`` in SQL (``count = count + n``), so two repeats racing
  each other both count.
* **A deleted account.** With ``require_account`` an observation is inserted only while the
  account row exists (and, given ``emitted_at``, was created no later than that instant): one
  ``INSERT ... SELECT ... WHERE EXISTS``, so a late signal can neither revive a deleted account's
  evidence nor land on a new account that reuses the id.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import and_, case, delete, exists, func, literal, or_, select, update

from core._schema_spam_evidence import _account_spam_evidence
from core._schema_tables import _accounts
from core.db import _get_engine
from schemas.spam_evidence import STATE_KIND_CLOSER, Evidence

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy import ColumnElement, Row
    from sqlalchemy.engine import Connection

_T = _account_spam_evidence
_STATE_KINDS = tuple(STATE_KIND_CLOSER)
# The kind whose newest unexpired row decides a limit and is never pruned.
_DECIDING_LIMIT = "spambot_limited"


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


def _insert_if_account_lives(
    connection: Connection, item: Evidence, emitted_at: datetime | None
) -> bool:
    """Insert the row only while its account exists (atomic); ``False`` when it does not."""
    values = _values(item)
    alive = and_(
        _accounts.c.account_id == item.account_id,
        *([] if emitted_at is None else [_accounts.c.created_at <= _iso(emitted_at)]),
    )
    source = select(*(literal(value).label(name) for name, value in values.items())).where(
        exists().where(alive)
    )
    return connection.execute(_T.insert().from_select(list(values), source)).rowcount > 0


def _merged_changes(newest: Row[Any], item: Evidence) -> bool:
    """The repeat brings a different end date or detail: what the fold reads has moved."""
    expires = None if item.expires_at is None else _iso(item.expires_at)
    return (newest.expires_at, newest.detail) != (expires, item.detail)


def _record_evidence(
    item: Evidence,
    window: timedelta,
    guard: tuple[bool, datetime | None],
) -> bool:
    require_account, emitted_at = guard
    with _get_engine().begin() as connection:
        newest = connection.execute(
            select(
                _T.c.id,
                _T.c.observed_at,
                _T.c.expires_at,
                _T.c.detail,
                _T.c.last_seen_at,
            )
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
                    # In SQL, not read-then-written: two repeats racing both count.
                    count=_T.c.count + item.count,
                    last_seen_at=func.max(_T.c.last_seen_at, _iso(seen)),
                    # The latest sighting's own facts replace the row's: a later date, a
                    # fresher reason. ``observed_at`` stays the first sighting.
                    expires_at=None if item.expires_at is None else _iso(item.expires_at),
                    operation=item.operation,
                    detail=item.detail,
                )
            )
            return _merged_changes(newest, item)
        if require_account:
            return _insert_if_account_lives(connection, item, emitted_at)
        connection.execute(_T.insert().values(**_values(item)))
    return True


async def record_evidence(
    item: Evidence,
    *,
    window: timedelta,
    require_account: bool = False,
    emitted_at: datetime | None = None,
) -> bool:
    """Store one observation; ``True`` when the fold has something new to read.

    That is a new row, or a repeat that brought a different end date or detail. A plain repeat
    only counts (``False``), and so does an observation of an account that is gone
    (``require_account``).
    """
    return await asyncio.to_thread(_record_evidence, item, window, (require_account, emitted_at))


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


def _not_protected(now: datetime) -> ColumnElement[bool]:
    """Rows retention may drop: not the newest of a state kind, nor the live @SpamBot limit.

    The newest row of each state kind per account stays, or an old freeze would stop being one.
    So does each account's newest ``spambot_limited`` that has not ended (undated, or its date
    ahead): it is what decides the account, and the cap must not trade it for newer noise.
    """
    states = (
        select(func.max(_T.c.id))
        .where(_T.c.kind.in_(_STATE_KINDS))
        .group_by(_T.c.account_id, _T.c.kind)
    )
    deciding = (
        select(func.max(_T.c.id))
        .where(
            _T.c.kind == _DECIDING_LIMIT,
            or_(_T.c.expires_at.is_(None), _T.c.expires_at > _iso(now)),
        )
        .group_by(_T.c.account_id)
    )
    return and_(_T.c.id.not_in(states), _T.c.id.not_in(deciding))


def _prune_evidence(
    before: datetime,
    max_per_account: int,
    account_id: str | None,
    now: datetime,
    shadow: tuple[frozenset[str], int],
) -> int:
    shadow_kinds, shadow_max = shadow
    unprotected = _not_protected(now)
    scope = unprotected if account_id is None else and_(unprotected, _T.c.account_id == account_id)
    is_shadow = _T.c.kind.in_(sorted(shadow_kinds))
    with _get_engine().begin() as connection:
        removed = connection.execute(
            delete(_T).where(_T.c.last_seen_at < _iso(before), unprotected)
        ).rowcount
        # Two quotas: real evidence ranks among real evidence, a shadow sighting among its own.
        ranked = (
            select(
                _T.c.id,
                is_shadow.label("shadow"),
                func.row_number()
                .over(
                    partition_by=(_T.c.account_id, is_shadow),
                    order_by=(_T.c.observed_at.desc(), _T.c.id.desc()),
                )
                .label("rank"),
            )
            .where(scope)
            .subquery()
        )
        excess = select(ranked.c.id).where(
            ranked.c.rank > case((ranked.c.shadow, shadow_max), else_=max_per_account)
        )
        removed += connection.execute(delete(_T).where(_T.c.id.in_(excess))).rowcount
    return removed


async def prune_evidence(  # noqa: PLR0913 - the one pruner; every knob is a keyword.
    before: datetime,
    max_per_account: int,
    *,
    account_id: str | None = None,
    now: datetime | None = None,
    shadow_kinds: frozenset[str] = frozenset(),
    shadow_max_per_account: int = 0,
) -> int:
    """Drop evidence last seen before ``before``, then the rows beyond each account's quotas.

    ``max_per_account`` caps the real rows and ``shadow_max_per_account`` the rows of
    ``shadow_kinds`` (kept separately: the newest of each). ``account_id`` limits the per-account
    caps to one account (the age cut-off is fleet-wide: it is one indexed range delete). The
    newest row of each state kind and the live @SpamBot limit of each account are always kept
    (``now`` decides which limit is live; default: the clock).
    """
    return await asyncio.to_thread(
        _prune_evidence,
        before,
        max_per_account,
        account_id,
        now or datetime.now(UTC),
        (shadow_kinds, shadow_max_per_account),
    )


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
