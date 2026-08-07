"""The readiness table's captcha give-up columns: the one-shot retry and its terminal state (#49).

Its own module rather than four more functions in ``_readiness.py``, which the file-size
gate would not take — the same split ``_bans.py`` already makes of the same table, and for
the same reason: one rule owns two columns nothing else writes, so the whole rule reads in
one place. ``core.db`` re-exports these via the package ``__init__``, so call sites are
unchanged.

The read below is a deliberate copy of ``_readiness._list_access_lost_readiness``'s shape
(inner select over channels, outer select over every row of them) and its predicate is the
twin of ``_readiness._ACCESS_LOST``; both docstrings say so, because a reader who finds one
must be able to find the other.

Public functions wrap sync helpers via ``asyncio.to_thread`` and return Pydantic models —
never raw rows (non-negotiable #2).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._challenges import _captcha_failed_since
from core.repositories.neurocomment._tables import _neurocomment_readiness
from schemas.neurocomment import NeurocommentReadiness, ReadinessList

# The guardian-bot captcha wall: a pair that IS in the group, has not passed the captcha
# and is therefore not comment-able. Unlike ``_readiness._ACCESS_LOST`` this triple is no
# sentinel anybody writes on purpose — it is simply what onboarding leaves behind when the
# solver loses — so ``_classify``'s ``_GATE_ERRORS`` branch (an admin mute) writes it too,
# and no captcha retry can help there. ``_captcha_failed_since`` is what tells the two
# apart. The three exclusions are ``_ACCESS_LOST``'s two plus this rule's own terminal
# state: onboarding refuses a skipped (#148), banned (#30) or given-up pair, so listing one
# would poke a solve that never runs — the defect that predicate's docstring names.
_CAPTCHA_BLOCKED = (
    (_neurocomment_readiness.c.joined == 1)
    & (_neurocomment_readiness.c.captcha_passed == 0)
    & (_neurocomment_readiness.c.ready == 0)
    & (_neurocomment_readiness.c.banned == 0)
    & (_neurocomment_readiness.c.human_skipped == 0)
    & (_neurocomment_readiness.c.captcha_gave_up == 0)
)


def _list_captcha_blocked_readiness(since: str) -> ReadinessList:
    channels = select(_neurocomment_readiness.c.channel).where(
        _CAPTCHA_BLOCKED & _captcha_failed_since(since),
    )
    statement = select(_neurocomment_readiness).where(
        _neurocomment_readiness.c.channel.in_(channels),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ReadinessList(
        readiness=[NeurocommentReadiness.model_validate(dict(row)) for row in rows],
    )


async def list_captcha_blocked_readiness(since: str) -> ReadinessList:
    """Readiness rows for every channel where a captcha has beaten a pair since ``since``.

    Shaped exactly like ``_readiness.list_access_lost_readiness``: the inner select picks
    the CHANNELS holding at least one blocked pair, the outer one returns EVERY readiness
    row of those channels. The give-up rule needs the siblings to prove nobody still works
    there before it unlinks the channel, and one stubborn pair must never drop a channel
    the other accounts comment in fine.

    ``since`` is a required ISO-8601 lower bound on the challenge failure, not a default:
    the challenges table is append-only and retention keeps failures for 90 days, so an
    unbounded read would let a row from a long-settled episode hand a pair a fresh retry.
    """
    return await asyncio.to_thread(_list_captcha_blocked_readiness, since)


def _stamp_captcha_retry(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            )
            .values(captcha_retry_at=_now_iso()),
        )


async def stamp_captcha_retry(account_id: str, channel: str) -> None:
    """Record that the ONE re-solve this pair gets has just been authorised.

    Separate from ``upsert_readiness`` for the reason ``stamp_rejoin_attempt`` is: a failed
    re-solve re-writes the readiness row with the very triple that asked for the retry, so a
    stamp carried by that write would reset itself and re-solve forever.

    A plain overwrite, not a COALESCE like ``stamp_join_request``: this column is a one-shot
    authorisation and its rule only ever writes it once (``_captcha_retry.retry_owed`` is
    false the moment it is set), so there is no second stamp to protect the first from.
    """
    await asyncio.to_thread(_stamp_captcha_retry, account_id, channel)


def _mark_captcha_gave_up(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel),
            )
            .values(captcha_gave_up=1, ready=0, checked_at=_now_iso()),
        )


async def mark_captcha_gave_up(account_id: str, channel: str) -> None:
    """Terminal: this pair stopped trying to pass the captcha and left the chat.

    A plain UPDATE like ``mark_pair_banned``, and ``ready=0`` for that write's reason:
    whatever the row claimed, a pair that has walked out of the discussion chat must never
    be selected again. The caller writes this BEFORE the leave RPC — the verdict is the
    truth and has to persist even if the leave dies.
    """
    await asyncio.to_thread(_mark_captcha_gave_up, account_id, channel)
