"""The readiness table's captcha give-up columns: the one-shot retry and its terminal state (#49).

Its own module rather than four more functions in ``_readiness.py``, which the file-size
gate would not take — the same split ``_bans.py`` already makes of the same table, and for
the same reason: one rule owns two columns nothing else writes, so the whole rule reads in
one place. ``core.db`` re-exports these via the package ``__init__``, so call sites are
unchanged.

The read below pairs with ``_readiness._list_access_lost_readiness`` and its predicate is
the twin of ``_readiness._ACCESS_LOST``; both docstrings say so, because a reader who finds
one must be able to find the other. It deliberately does NOT copy that read's shape any
more (inner select over channels, outer select over every row of them) — see the read's own
docstring for the pair that shape retired for somebody else's captcha.

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
    statement = select(_neurocomment_readiness).where(
        _CAPTCHA_BLOCKED & _captcha_failed_since(since),
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ReadinessList(
        readiness=[NeurocommentReadiness.model_validate(dict(row)) for row in rows],
    )


async def list_captcha_blocked_readiness(since: str) -> ReadinessList:
    """Readiness rows of the pairs a captcha has beaten since ``since`` — and only those.

    Every row satisfies BOTH halves of the rule: the readiness triple AND a failed
    challenge of its own. It used to return every row of any channel holding one such
    pair, on the theory that the give-up rule needs the siblings before it unlinks a
    channel — but that rule re-reads them itself (``_drop_channel_if_nobody_passed`` →
    ``list_channel_readiness``), so the extra rows fed nothing except the caller's
    ``captcha_blocked`` filter, which is the readiness half alone. An admin mute writes
    that same triple with no challenge row, so a muted account sharing a channel with a
    captcha-blocked one was stamped, retried, then marked ``captcha_gave_up`` and walked
    out of a group it had never fought a captcha in. The channel indirection WAS that bug;
    the single-account case only ever looked safe because the mute alone selected no
    channel at all.

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
    authorisation and its rule only ever writes it once per EPISODE
    (``_captcha_retry.retry_owed`` is false the moment it is set), so there is no second
    stamp to protect the first from — a later episode writes into the NULL
    ``clear_captcha_retry`` left behind, and a COALESCE would pin that stamp to the wrong one.
    """
    await asyncio.to_thread(_stamp_captcha_retry, account_id, channel)


def _clear_captcha_retry(account_id: str, channel: str) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            update(_neurocomment_readiness)
            .where(
                # Only rows actually carrying a stamp — the caller runs on the post hot
                # path, and a pair that never met a guardian bot must not pay an UPDATE per
                # delivered comment to write the NULL already there. The filter
                # ``_clear_join_request`` and ``clear_rejoin_attempts`` both use.
                (_neurocomment_readiness.c.account_id == account_id)
                & (_neurocomment_readiness.c.channel == channel)
                & _neurocomment_readiness.c.captcha_retry_at.is_not(None),
            )
            .values(captcha_retry_at=None),
        )


async def clear_captcha_retry(account_id: str, channel: str) -> None:
    """Give this pair its one re-solve back: the episode that spent it is over.

    ``captcha_gave_up`` deliberately does NOT ride along, unlike ``rejoin_gave_up`` in
    ``clear_rejoin_attempts``. That verdict is terminal here — the account has WALKED OUT of
    the discussion group and onboarding refuses it from then on, so a pair carrying it cannot
    be selected, cannot comment, and can never reach this call. Clearing it would be a reset
    nothing asked for and nothing could observe.
    """
    await asyncio.to_thread(_clear_captcha_retry, account_id, channel)


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
