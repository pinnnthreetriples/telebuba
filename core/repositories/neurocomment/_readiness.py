"""Per-(account, channel) readiness reads and writes.

Split from ``_comments.py`` for the file-size budget (mirrors ``_bans.py``, which
already owns the ban columns of the same table). ``core.db`` re-exports these via
the package ``__init__``, so call sites are unchanged.

Public functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic
models / ``None`` — never raw rows (non-negotiable #2).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_campaign_accounts,
    _neurocomment_campaign_channels,
    _neurocomment_readiness,
)
from schemas.neurocomment import NeurocommentReadiness, ReadinessList


def _fetch_readiness(account_id: str, channel: str) -> NeurocommentReadiness | None:
    statement = select(_neurocomment_readiness).where(
        (_neurocomment_readiness.c.account_id == account_id)
        & (_neurocomment_readiness.c.channel == channel),
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).mappings().first()
    return None if row is None else NeurocommentReadiness.model_validate(dict(row))


async def fetch_readiness(account_id: str, channel: str) -> NeurocommentReadiness | None:
    return await asyncio.to_thread(_fetch_readiness, account_id, channel)


def _upsert_readiness(
    account_id: str,
    channel: str,
    *,
    joined: bool,
    captcha_passed: bool,
    ready: bool,
) -> NeurocommentReadiness:
    fields = {
        "joined": int(joined),
        "captcha_passed": int(captcha_passed),
        "ready": int(ready),
        "checked_at": _now_iso(),
    }
    statement = (
        sqlite_insert(_neurocomment_readiness)
        .values(account_id=account_id, channel=channel, **fields)
        .on_conflict_do_update(
            index_elements=[
                _neurocomment_readiness.c.account_id,
                _neurocomment_readiness.c.channel,
            ],
            set_=fields,
        )
    )
    with _get_engine().begin() as connection:
        connection.execute(statement)
    record = _fetch_readiness(account_id, channel)
    if record is None:  # pragma: no cover - upsert above guarantees the row
        msg = f"Readiness was not persisted: {account_id!r}/{channel!r}"
        raise RuntimeError(msg)
    return record


async def upsert_readiness(
    account_id: str,
    channel: str,
    *,
    joined: bool,
    captcha_passed: bool,
    ready: bool,
) -> NeurocommentReadiness:
    """Record per-(account, channel) join/captcha/ready state at onboarding.

    Leaves ``human_skipped`` untouched (an operator skip survives a re-onboard).
    """
    return await asyncio.to_thread(
        _upsert_readiness,
        account_id,
        channel,
        joined=joined,
        captcha_passed=captcha_passed,
        ready=ready,
    )


def _stamp_join_request(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            )
            .values(
                join_requested_at=_now_iso(),
                join_request_attempts=_neurocomment_readiness.c.join_request_attempts + 1,
            ),
        )


async def stamp_join_request(account_id: str, channel: str) -> None:
    """Record that an approval-gated join request just went out for this pair.

    Deliberately NOT part of ``upsert_readiness``: the join-request branch upserts the
    readiness row first and stamps here, so a plain re-onboard (which re-upserts) cannot
    reset the counter — that reset is exactly what let the same pair re-request forever.
    """
    await asyncio.to_thread(_stamp_join_request, account_id, channel)


def _clear_join_request(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                # Only rows that actually hold a stamp — a normal join must not pay an
                # UPDATE on every onboarding pass just to write the values already there.
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel)
                & _neurocomment_readiness.c.join_requested_at.is_not(None),
            )
            .values(join_requested_at=None, join_request_attempts=0),
        )


async def clear_join_request(account_id: str, channel: str) -> None:
    """Forget a pending join request once the pair is in the group (approval landed)."""
    await asyncio.to_thread(_clear_join_request, account_id, channel)


def _list_pending_join_readiness() -> ReadinessList:
    # Every readiness row of every channel that has at least one outstanding request —
    # not just the pending rows. The sweep's give-up rule needs BOTH halves in one read:
    # the pending stamps to age, and the sibling rows to prove no account is ready there
    # (one stubborn account must never kill a channel the others comment in fine).
    channels = select(_neurocomment_readiness.c.channel).where(
        _neurocomment_readiness.c.join_requested_at.is_not(None),
    )
    statement = select(_neurocomment_readiness).where(
        _neurocomment_readiness.c.channel.in_(channels),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ReadinessList(
        readiness=[NeurocommentReadiness.model_validate(dict(row)) for row in rows],
    )


async def list_pending_join_readiness() -> ReadinessList:
    """Readiness rows for every channel holding at least one pending join request."""
    return await asyncio.to_thread(_list_pending_join_readiness)


def _mark_human_skipped(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            )
            .values(human_skipped=1, ready=0, checked_at=_now_iso()),
        )


async def mark_human_skipped(account_id: str, channel: str) -> None:
    """Operator skip: the engine never selects this pair (ready=0, human_skipped=1)."""
    await asyncio.to_thread(_mark_human_skipped, account_id, channel)


def _delete_readiness(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            delete(_neurocomment_readiness).where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            ),
        )


async def delete_readiness(account_id: str, channel: str) -> None:
    """Erase a pair's readiness so a retry re-onboards from scratch (clears the skip)."""
    await asyncio.to_thread(_delete_readiness, account_id, channel)


def _list_campaign_readiness(campaign_id: str) -> ReadinessList:
    # Readiness is per-(account, channel); scope to the campaign's accounts AND its
    # channels so the board reads every pair in one query instead of N per-card
    # fetches. Scoping by channels too keeps an account shared across campaigns from
    # leaking the other campaign's (account, channel) rows onto this card.
    accounts = select(_neurocomment_campaign_accounts.c.account_id).where(
        _neurocomment_campaign_accounts.c.campaign_id == campaign_id,
    )
    channels = select(_neurocomment_campaign_channels.c.channel).where(
        (_neurocomment_campaign_channels.c.campaign_id == campaign_id)
        & (_neurocomment_campaign_channels.c.active == 1),
    )
    statement = select(_neurocomment_readiness).where(
        _neurocomment_readiness.c.account_id.in_(accounts)
        & _neurocomment_readiness.c.channel.in_(channels),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ReadinessList(
        readiness=[NeurocommentReadiness.model_validate(dict(row)) for row in rows],
    )


async def list_campaign_readiness(campaign_id: str) -> ReadinessList:
    """All readiness rows for a campaign's accounts (bulk read for the board)."""
    return await asyncio.to_thread(_list_campaign_readiness, campaign_id)


def _list_channel_readiness(
    campaign_id: str,
    channel: str,
    account_ids: list[str],
) -> ReadinessList:
    if not account_ids:
        return ReadinessList()
    # Per-post equivalent of _list_campaign_readiness: both filters in SQL return
    # <= len(account_ids) rows instead of accounts x channels. The campaign-accounts
    # subquery still blocks the shared-account leak documented above; the (account_id,
    # channel) PK makes it a SEARCH (verified via EXPLAIN QUERY PLAN).
    accounts = select(_neurocomment_campaign_accounts.c.account_id).where(
        _neurocomment_campaign_accounts.c.campaign_id == campaign_id,
    )
    statement = select(_neurocomment_readiness).where(
        _neurocomment_readiness.c.account_id.in_(accounts)
        & _neurocomment_readiness.c.account_id.in_(account_ids)
        & (_neurocomment_readiness.c.channel == channel),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ReadinessList(
        readiness=[NeurocommentReadiness.model_validate(dict(row)) for row in rows],
    )


async def list_channel_readiness(
    campaign_id: str,
    channel: str,
    account_ids: list[str],
) -> ReadinessList:
    """Readiness rows for one channel and the given campaign accounts (per-post read)."""
    return await asyncio.to_thread(_list_channel_readiness, campaign_id, channel, account_ids)
