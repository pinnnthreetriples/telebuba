"""Counts over the send journal: the three quota predicates and the progress numerator.

Split from ``_messages`` for the file-size budget, not for layering — every query here
reads the same table those writes fill.

**The predicate is ``status IN ('pending','sent')`` everywhere a cap is scored**, and
that is not a detail. A row only becomes ``sent`` after the dispatch returns, so a
count that asked for ``sent`` alone would be blind to every send currently in flight:
two campaigns sharing an account would each read an under-cap total and both dispatch.
Counting the ``pending`` row the sender wrote before it dispatched is what makes the
re-count under the per-account lock tell the truth. ``skipped`` and ``failed`` are
excluded because nothing was published for them.

The SELECTION score is not a cap and counts ``failed`` as well, which is the one place
the two predicates part company — an account whose sends all fail would otherwise score
zero for ever and win every tie-break.

The progress numerator is the opposite question and uses ``sent`` alone: it reports
what actually reached the chats, restricted to ``message`` steps because a reaction is
not a message and skipping one must not drag the percentage down.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, Select, func, select

from core.db import _get_engine
from core.repositories.neuroshilling._tables import (
    _neuroshilling_messages,
    _neuroshilling_steps,
    run_scope,
)
from schemas.neuroshilling import NeuroshillingQuotaUsage

if TYPE_CHECKING:
    from collections.abc import Sequence

_TABLE = _neuroshilling_messages
# Rows that have consumed a slot: one already published, one about to be.
_SPENT = ("pending", "sent")
# The load score's own predicate, and deliberately wider than the quota's. A ``failed``
# row published nothing, so it must not consume a cap — but it IS an attempt this
# account made, and leaving it out made the account that fails every step score zero
# forever: always the minimum, always the pick, while a working sibling of the same role
# was never chosen and the whole dialogue failed in that chat.
_ATTEMPTED = ("pending", "sent", "failed")
# Every count here joins the step it belongs to, because all of them are counts of
# MESSAGES. The operator's fields say "messages per hour" and "messages per chat per
# day", and scoring a reaction against them would make those numbers mean something
# the form does not say — the same reason the progress numerator filters the same way.
_MESSAGE_ROWS = _TABLE.join(
    _neuroshilling_steps,
    _TABLE.c.step_id == _neuroshilling_steps.c.step_id,
)
_IS_MESSAGE = _neuroshilling_steps.c.kind == "message"


def _spent_count(*conditions: ColumnElement[bool]) -> Select[tuple[int]]:
    """``SELECT COUNT(*)`` over spent MESSAGE rows, narrowed by ``conditions``."""
    return (
        select(func.count())
        .select_from(_MESSAGE_ROWS)
        .where(_TABLE.c.status.in_(_SPENT), _IS_MESSAGE, *conditions)
    )


def _read_quota_usage(
    campaign_id: str,
    account_id: str,
    target: str,
    hour_since: str,
    day_since: str,
) -> NeuroshillingQuotaUsage:
    by_account = _TABLE.c.account_id == account_id
    hour = _spent_count(by_account, _TABLE.c.created_at >= hour_since)
    chat_day = _spent_count(
        by_account,
        _TABLE.c.target == target,
        _TABLE.c.created_at >= day_since,
    )
    lifetime = _spent_count(by_account, _TABLE.c.campaign_id == campaign_id)
    with _get_engine().connect() as connection:
        return NeuroshillingQuotaUsage(
            hour=connection.execute(hour).scalar_one(),
            chat_day=connection.execute(chat_day).scalar_one(),
            campaign_total=connection.execute(lifetime).scalar_one(),
        )


async def read_quota_usage(
    campaign_id: str,
    account_id: str,
    target: str,
    *,
    hour_since: str,
    day_since: str,
) -> NeuroshillingQuotaUsage:
    """All three cap counters for one account in one thread hop.

    Together rather than one function each because they are always asked together,
    under the account's quota lock, and three hops would hold that lock across three
    scheduling points instead of one.

    Counted over the account's WHOLE history rather than over this run: Telegram rate-
    limits the account, and a fresh run is not a fresh account.
    """
    return await asyncio.to_thread(
        _read_quota_usage,
        campaign_id,
        account_id,
        target,
        hour_since,
        day_since,
    )


def _count_messages_since(account_ids: Sequence[str], since_iso: str) -> dict[str, int]:
    statement = (
        select(_TABLE.c.account_id, func.count())
        .select_from(_MESSAGE_ROWS)
        .where(
            _TABLE.c.account_id.in_(account_ids)
            & _TABLE.c.status.in_(_ATTEMPTED)
            & _IS_MESSAGE
            & (_TABLE.c.created_at >= since_iso),
        )
        .group_by(_TABLE.c.account_id)
    )
    with _get_engine().connect() as connection:
        return {str(account_id): int(count) for account_id, count in connection.execute(statement)}


async def count_messages_since(
    account_ids: Sequence[str],
    since_iso: str,
) -> dict[str, int]:
    """How much each of ``account_ids`` has ATTEMPTED since ``since_iso``.

    One grouped query rather than one per candidate: this is the load signal the step
    picks its speaker by, and it is asked once per step of every target.
    Accounts with nothing in the window are simply absent from the mapping.

    Failures count here and nowhere else — see ``_ATTEMPTED``. The score exists to
    spread work off a busy session, and an account that cannot send at all is the
    busiest thing in the pool for the purpose of not picking it again.
    """
    if not account_ids:
        return {}
    return await asyncio.to_thread(_count_messages_since, list(account_ids), since_iso)


def _count_sent_message_steps(run_id: str) -> int:
    statement = (
        select(func.count())
        .select_from(_MESSAGE_ROWS)
        .where(run_scope(run_id) & (_TABLE.c.status == "sent") & _IS_MESSAGE)
    )
    with _get_engine().connect() as connection:
        return int(connection.execute(statement).scalar_one())


async def count_sent_message_steps(run_id: str) -> int:
    """The progress numerator: delivered MESSAGE steps of this run.

    Reactions are journalled like everything else but excluded here, because the
    denominator is targets x message steps — counting a reaction in one and not the
    other is how a progress bar ends up past its own total.

    ``run_scope`` folds a revive campaign's per-cycle keys in, so its counter keeps
    climbing across cycles instead of resetting to zero every time round. That run
    has no denominator to be measured against — the card shows the count itself.
    """
    return await asyncio.to_thread(_count_sent_message_steps, run_id)
