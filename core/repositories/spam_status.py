"""Spam-status cache repository (split out of core.db for #38).

Owns reads/writes of the ``account_spam_status`` table. Shared plumbing (engine,
table object, row helpers) is imported from ``core.db``; the public async
functions are re-exported by ``core.db`` so callers are unaffected.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import exists, insert, literal, or_, select, update

from core._schema_tables import _accounts
from core.db import _account_spam_status, _get_engine, _optional_str
from schemas.spam_evidence import StoredProjection, Verdict
from schemas.spam_status import SpamStatusKind, SpamStatusVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping


# A row that exists only to carry the folded projection (no @SpamBot reading has been stored
# for the account) has this ``checked_at``. Every reader below skips it, so the pre-evidence
# code sees exactly the rows it saw before: no reading, no row.
_PROJECTION_ONLY = ""
_VISIBLE = _account_spam_status.c.checked_at != _PROJECTION_ONLY


def _row_to_spam_status(mapping: Mapping[str, object]) -> SpamStatusVerdict:
    return SpamStatusVerdict(
        account_id=str(mapping["account_id"]),
        status=cast("SpamStatusKind", str(mapping["status"])),
        detail=_optional_str(mapping.get("detail")),
        checked_at=str(mapping["checked_at"]),
    )


def _get_spam_status(account_id: str) -> SpamStatusVerdict | None:
    statement = select(_account_spam_status).where(
        _account_spam_status.c.account_id == account_id,
        _VISIBLE,
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    if row is None:
        return None
    return _row_to_spam_status(cast("Mapping[str, object]", row))


async def get_spam_status(account_id: str) -> SpamStatusVerdict | None:
    """Return the cached spam-status verdict for an account, or ``None``."""
    return await asyncio.to_thread(_get_spam_status, account_id)


def _list_spam_statuses() -> dict[str, SpamStatusVerdict]:
    with _get_engine().connect() as connection:
        rows = connection.execute(select(_account_spam_status).where(_VISIBLE)).mappings().all()
    return {
        str(row["account_id"]): _row_to_spam_status(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_spam_statuses() -> dict[str, SpamStatusVerdict]:
    """Return every cached spam-status verdict keyed by ``account_id`` (one query)."""
    return await asyncio.to_thread(_list_spam_statuses)


def _list_spam_statuses_by_ids(account_ids: list[str]) -> dict[str, SpamStatusVerdict]:
    if not account_ids:
        return {}
    statement = select(_account_spam_status).where(
        _account_spam_status.c.account_id.in_(account_ids),
        _VISIBLE,
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_spam_status(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_spam_statuses_by_ids(account_ids: list[str]) -> dict[str, SpamStatusVerdict]:
    """Cached spam-status verdicts for a set of accounts keyed by ``account_id``."""
    return await asyncio.to_thread(_list_spam_statuses_by_ids, account_ids)


def _upsert_spam_status(verdict: SpamStatusVerdict) -> SpamStatusVerdict:
    values = {
        "status": verdict.status,
        "detail": verdict.detail,
        "checked_at": verdict.checked_at,
    }
    with _get_engine().begin() as connection:
        updated = connection.execute(
            update(_account_spam_status)
            .where(_account_spam_status.c.account_id == verdict.account_id)
            .values(**values),
        )
        if updated.rowcount == 0:
            connection.execute(
                insert(_account_spam_status).values(account_id=verdict.account_id, **values),
            )
    return verdict


async def upsert_spam_status(verdict: SpamStatusVerdict) -> SpamStatusVerdict:
    """Insert or update one account's cached spam-status verdict."""
    return await asyncio.to_thread(_upsert_spam_status, verdict)


def _stamp(value: object) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _row_to_projection(mapping: Mapping[str, object]) -> StoredProjection:
    legacy = None if mapping["checked_at"] == _PROJECTION_ONLY else _row_to_spam_status(mapping)
    verdict = None
    if mapping.get("fold_status") is not None:
        reason = mapping.get("fold_reason")
        verdict = Verdict.model_validate(
            {
                "status": mapping["fold_status"],
                "confidence": mapping.get("confidence") or "low",
                "valid_until": _stamp(mapping.get("valid_until")),
                "primary_kind": mapping.get("primary_kind"),
                "primary_observed_at": _stamp(mapping.get("primary_observed_at")),
                "last_spambot_at": _stamp(mapping.get("last_spambot_at")),
                "needs_probe": bool(mapping.get("needs_probe")),
                "contradiction": reason == "contradiction",
                "reason": reason,
                "tos_restricted": bool(mapping.get("tos_restricted")),
                "tos_reason": _optional_str(mapping.get("tos_reason")),
            }
        )
    return StoredProjection(
        account_id=str(mapping["account_id"]),
        verdict=verdict,
        folded_at=_stamp(mapping.get("folded_at")),
        legacy=legacy,
    )


def _list_projections_by_ids(account_ids: list[str]) -> dict[str, StoredProjection]:
    if not account_ids:
        return {}
    statement = select(_account_spam_status).where(
        _account_spam_status.c.account_id.in_(account_ids),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return {
        str(row["account_id"]): _row_to_projection(cast("Mapping[str, object]", row))
        for row in rows
    }


async def list_projections_by_ids(account_ids: list[str]) -> dict[str, StoredProjection]:
    """What the table holds for each account: the folded projection and the legacy reading."""
    return await asyncio.to_thread(_list_projections_by_ids, account_ids)


def _store_projection(account_id: str, verdict: Verdict, folded_at: datetime) -> bool:
    folded = folded_at.astimezone(UTC).isoformat(timespec="microseconds")
    values = {
        "confidence": verdict.confidence,
        "valid_until": None if verdict.valid_until is None else verdict.valid_until.isoformat(),
        "primary_kind": verdict.primary_kind,
        "primary_observed_at": (
            None if verdict.primary_observed_at is None else verdict.primary_observed_at.isoformat()
        ),
        "last_spambot_at": (
            None if verdict.last_spambot_at is None else verdict.last_spambot_at.isoformat()
        ),
        "needs_probe": int(verdict.needs_probe),
        "fold_status": verdict.status,
        "fold_reason": verdict.reason,
        "tos_restricted": int(verdict.tos_restricted),
        "tos_reason": verdict.tos_reason,
        "folded_at": folded,
    }
    table = _account_spam_status
    with _get_engine().begin() as connection:
        # A fold never overwrites a NEWER one: a write that was delayed (its evidence read
        # before a later write landed) finds ``folded_at`` ahead of its own and does nothing.
        newer = connection.execute(
            select(table.c.folded_at).where(table.c.account_id == account_id)
        ).first()
        if newer is not None and newer.folded_at is not None and str(newer.folded_at) > folded:
            return False
        updated = connection.execute(
            update(table)
            .where(
                table.c.account_id == account_id,
                or_(table.c.folded_at.is_(None), table.c.folded_at <= folded),
            )
            .values(**values),
        )
        if updated.rowcount == 0 and newer is None:
            # ``status`` and ``checked_at`` are NOT NULL: a projection-only row carries a
            # neutral ``unknown`` and the marker ``checked_at`` the readers skip. Only while
            # the account exists: a fold that outlived its account writes nothing.
            row = {
                "account_id": account_id,
                "status": "unknown",
                "detail": None,
                "checked_at": _PROJECTION_ONLY,
                **values,
            }
            source = select(*(literal(value).label(name) for name, value in row.items())).where(
                exists().where(_accounts.c.account_id == account_id)
            )
            return connection.execute(table.insert().from_select(list(row), source)).rowcount > 0
    return True


async def store_projection(account_id: str, verdict: Verdict, folded_at: datetime) -> bool:
    """Write the folded projection of an account, leaving ``status``/``detail``/``checked_at``.

    An account with no stored reading gets a projection-only row that the legacy readers
    (``get_spam_status`` and the lists) do not see. ``False`` when nothing was written: a NEWER
    fold is already stored (this one was computed from older evidence), or the account is gone.
    """
    return await asyncio.to_thread(_store_projection, account_id, verdict, folded_at)
