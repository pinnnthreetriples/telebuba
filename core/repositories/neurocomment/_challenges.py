"""Challenge audit-and-cache queries (Ф2 #120).

One table backs both the audit log and the global solved-decision cache. This
slice (#145) is detection-only: ``insert_challenge`` appends a row and
``list_failed_for_channel`` powers the operator drill-down. The cache-lookup +
outcome-resolution readers land with the solver slice.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, true, tuple_, update

from core.db import _get_engine, _now_iso
from core.repositories.neurocomment._tables import (
    _neurocomment_challenges,
    _neurocomment_readiness,
)
from schemas.challenge import (
    AccountChannel,
    ChallengedChannels,
    ChallengeDecision,
    ChallengeInsert,
    ChallengeOutcomeCounts,
    ChallengeRow,
    ChallengeRowList,
)

if TYPE_CHECKING:
    from sqlalchemy import RowMapping
    from sqlalchemy.sql.elements import ColumnElement

# Non-solved outcomes the drill-down surfaces ("what broke the solver"); a
# resolved/pending row is not a failure to show.
_FAILED_OUTCOMES = ("give_up", "failed")


def _still_blocked() -> ColumnElement[bool]:
    """Exclude a failure whose pair has since passed its captcha *from inside the chat*.

    The table is append-only: a solve inserts a new row and never rewrites the old
    ``give_up``, and retention keeps failures for 90 days. So without this the queue
    counted long-resolved failures forever — after the thinking-budget fix it still
    claimed six pairs needed a human while five of them were already ``ready``, which
    is exactly the false alarm that sent an operator looking for a bug that was fixed.
    Readiness is the live answer to "is this pair still captcha-blocked"; a pair with
    no readiness row at all (a retry erased it) stays listed, since nothing yet proves
    it passed.

    ``captcha_passed=1`` alone does NOT mean that, which is the trap this docstring
    exists to name: ``(joined=0, captcha_passed=1, ready=0)`` is the hard-join-failure /
    lost-access sentinel that ``_classify`` and ``_outcomes`` write, and the flag is
    load-bearing there (``_rejoin.access_lost`` and ``_readiness._ACCESS_LOST`` both key
    on it), so it cannot be spelled any other way. Only a pair still IN the group can
    have passed anything.
    """
    passed = (
        select(_neurocomment_readiness.c.account_id)
        .where(
            (_neurocomment_readiness.c.account_id == _neurocomment_challenges.c.account_id)
            & (_neurocomment_readiness.c.channel == _neurocomment_challenges.c.channel)
            & (_neurocomment_readiness.c.captcha_passed == 1)
            # Without this a kick — which lands on the pair long AFTER the solver gave up —
            # silently deleted its ``give_up`` from the operator's drill-down, as if a human
            # had already dealt with it. Nothing had. ``joined=1`` costs no legitimate
            # exclusion: the only writer of a joined+passed row is the ``ready`` state.
            & (_neurocomment_readiness.c.joined == 1),
        )
        .exists()
    )
    return ~passed


def _captcha_failed_since(since: str) -> ColumnElement[bool]:
    """The pair of THIS readiness row has a failed challenge no older than ``since``.

    The mirror image of the two predicates around it: they are challenges-rooted
    subqueries reaching into readiness, and this one is readiness-rooted and reaches into
    challenges. It is the discriminator the captcha give-up rule keys on — the readiness
    triple ``(joined=1, captcha_passed=0, ready=0)`` is written both by a bot gate the
    solver lost to and by ``_classify``'s ``_GATE_ERRORS`` branch (an admin mute), and
    only the first is a captcha anyone could retry. A challenge row is what tells them
    apart; ``since`` bounds it so a failure from a retired episode cannot revive a pair.

    Deliberately NOT wrapped in :func:`_still_blocked`, and the omission is the one thing
    worth stating here because the two otherwise look like they disagree: that helper
    exists to prove, from the challenges side, that the pair has not since passed its
    captcha *from inside the chat*. Here the readiness half of the caller's query already
    asserts ``captcha_passed == 0`` for the very same pair, which is the same fact read
    off the live row rather than inferred around the append-only table. Applying it too
    would be a second copy of one claim, and a copy is what drifts.
    """
    return (
        select(_neurocomment_challenges.c.id)
        .where(
            (_neurocomment_challenges.c.account_id == _neurocomment_readiness.c.account_id)
            & (_neurocomment_challenges.c.channel == _neurocomment_readiness.c.channel)
            & _neurocomment_challenges.c.outcome.in_(_FAILED_OUTCOMES)
            & (_neurocomment_challenges.c.decided_at >= since),
        )
        .exists()
    )


def _retry_can_reach() -> ColumnElement[bool]:
    """Exclude a failure whose pair the captcha rule has already finished with.

    Since #49 the queue answers one question — which pairs is the automatic rule working
    on right now — and it holds no control of any kind, so a listed row is a claim that
    something is still in progress. These three states are each a way of being done, and
    listing one states the opposite of the truth on a panel whose whole job is to be
    believed. (Before #49 the same three were excluded for the opposite reason: each row
    carried a «Повторить» button that DELETED the readiness row, so the button worked on
    exactly the pairs it must not work on. The button is gone; the exclusions are not.)

    A ban (#30) is permanent by design, and ``services.neurocomment.bans`` defends itself
    with the invariant "a banned pair has no challenge row" — an invariant nothing enforced
    until this predicate. An operator skip (#148) is a decision to take the pair off this
    channel; a queue that reads like live work must not show it as pending. And a pair that
    gave up on the captcha (#49) has spent its one retry and walked out of the discussion
    chat — nothing is being solved there, now or later.

    The other two states the queue drops are the caller's, because both need reads core has
    no business making: a paused channel and a spent re-join budget.
    """
    refused = (
        select(_neurocomment_readiness.c.account_id)
        .where(
            (_neurocomment_readiness.c.account_id == _neurocomment_challenges.c.account_id)
            & (_neurocomment_readiness.c.channel == _neurocomment_challenges.c.channel)
            & (
                (_neurocomment_readiness.c.banned == 1)
                | (_neurocomment_readiness.c.human_skipped == 1)
                | (_neurocomment_readiness.c.captcha_gave_up == 1)
            ),
        )
        .exists()
    )
    return ~refused


def _insert_challenge(row: ChallengeInsert) -> None:
    with _get_engine().begin() as connection:
        connection.execute(
            _neurocomment_challenges.insert().values(
                challenge_hash=row.challenge_hash,
                account_id=row.account_id,
                channel=row.channel,
                raw_text=row.raw_text,
                button_labels_json=json.dumps(row.button_labels, ensure_ascii=False),
                decision_json=row.decision_json,
                outcome=row.outcome,
                decided_at=_now_iso(),
                outcome_at=None,
            ),
        )


async def insert_challenge(row: ChallengeInsert) -> None:
    """Append one challenge audit row (audit + global cache share this table)."""
    await asyncio.to_thread(_insert_challenge, row)


def _decision_reasoning(decision_json: object) -> str | None:
    if not decision_json:
        return None
    try:
        reasoning = json.loads(str(decision_json)).get("reasoning")
    except (ValueError, AttributeError):
        return None
    return str(reasoning) if reasoning is not None else None


def _row_to_challenge(row: RowMapping) -> ChallengeRow:
    return ChallengeRow(
        account_id=str(row["account_id"]),
        channel=str(row["channel"]),
        raw_text=str(row["raw_text"]),
        button_labels=list(json.loads(row["button_labels_json"])),
        outcome=str(row["outcome"]),
        decided_at=str(row["decided_at"]),
        reasoning=_decision_reasoning(row["decision_json"]),
    )


def _list_failed_for_channel(channel: str, limit: int) -> ChallengeRowList:
    # Order by id as the tiebreaker: same-microsecond inserts still come back
    # newest-first deterministically.
    statement = (
        select(_neurocomment_challenges)
        .where(
            (_neurocomment_challenges.c.channel == channel)
            & _neurocomment_challenges.c.outcome.in_(_FAILED_OUTCOMES)
            & _still_blocked(),
        )
        .order_by(
            _neurocomment_challenges.c.decided_at.desc(),
            _neurocomment_challenges.c.id.desc(),
        )
        .limit(limit)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ChallengeRowList(rows=[_row_to_challenge(row) for row in rows])


async def list_failed_for_channel(channel: str, limit: int) -> ChallengeRowList:
    """Most-recent non-solved challenges for a channel (operator drill-down)."""
    return await asyncio.to_thread(_list_failed_for_channel, channel, limit)


def _not_one_of(pairs: list[AccountChannel]) -> ColumnElement[bool]:
    """``(account_id, channel)`` is none of ``pairs`` — the caller's own exclusion list."""
    return ~tuple_(
        _neurocomment_challenges.c.account_id,
        _neurocomment_challenges.c.channel,
    ).in_([(pair.account_id, pair.channel) for pair in pairs])


def _list_failed_for_channels(
    channels: list[str],
    limit: int,
    since: str,
    exclude_pairs: list[AccountChannel],
) -> ChallengeRowList:
    if not channels:
        return ChallengeRowList()
    statement = (
        select(_neurocomment_challenges)
        .where(
            _neurocomment_challenges.c.channel.in_(channels)
            & _neurocomment_challenges.c.outcome.in_(_FAILED_OUTCOMES)
            & (_neurocomment_challenges.c.decided_at >= since)
            & _still_blocked()
            & _retry_can_reach()
            & (_not_one_of(exclude_pairs) if exclude_pairs else true()),
        )
        .order_by(
            _neurocomment_challenges.c.decided_at.desc(),
            _neurocomment_challenges.c.id.desc(),
        )
        .limit(limit)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return ChallengeRowList(rows=[_row_to_challenge(row) for row in rows])


async def list_failed_for_channels(
    channels: list[str],
    limit: int,
    since: str,
    exclude_pairs: list[AccountChannel],
) -> ChallengeRowList:
    """Actionable non-solved challenges across ``channels``, newest first (the captcha queue).

    ``since`` is an ISO-8601 lower bound on ``decided_at`` — required, not defaulted,
    because an empty bound is exactly the unbounded queue this argument exists to end.

    ``exclude_pairs`` carries the one rule this layer cannot spell (the re-join budget: it
    needs ``settings`` and a verdict set that live in services), as a pair list rather than
    as re-implemented SQL, so there is still exactly one definition of "finished". EVERY
    exclusion has to be inside this statement, because ``limit`` is applied by the database:
    filtering afterwards let 24 hidden rows — six pairs, four challenge rows each, all
    inside the age window — fill a 20-row queue and hide the one pair a human could act on.
    """
    return await asyncio.to_thread(
        _list_failed_for_channels,
        channels,
        limit,
        since,
        exclude_pairs,
    )


def _list_challenged_channels(channels: list[str]) -> ChallengedChannels:
    if not channels:
        return ChallengedChannels()
    statement = (
        select(_neurocomment_challenges.c.channel)
        .where(
            _neurocomment_challenges.c.channel.in_(channels)
            & _neurocomment_challenges.c.outcome.in_(_FAILED_OUTCOMES)
            & _still_blocked(),
        )
        .distinct()
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    return ChallengedChannels(channels=[str(row[0]) for row in rows])


async def list_challenged_channels(channels: list[str]) -> ChallengedChannels:
    """Which of ``channels`` still carry an UNRESOLVED challenge (bulk board signal).

    ``_still_blocked`` for the reason it exists on the queue, which this read wants just as
    much and was the only one of the three not to have: the table is append-only, so a pair
    that later solved its captcha leaves its old ``give_up`` behind forever. Without the
    exclusion one long-resolved row made ``board._channel_status`` answer ``bot_challenge``
    for a channel blocked by something else entirely — the badge picks between "a guardian
    bot is the wall" and ``chat_restricted`` on exactly this signal, so a stale row did not
    merely age, it named the wrong wall and sent the operator after the wrong fix.

    Deliberately NOT the queue's other filters. The badge answers "why is this channel
    stuck", the queue answers "what can a human press today", and the two are allowed to
    differ: dropping rows past ``challenge_queue_max_age_days`` here would report
    ``chat_restricted`` for a channel a bot gate really is holding, and dropping a skipped
    pair's row would do the same. Both trade a true diagnosis for a false one.
    """
    return await asyncio.to_thread(_list_challenged_channels, channels)


def _lookup_cached_decision(challenge_hash: str) -> ChallengeDecision | None:
    statement = (
        select(_neurocomment_challenges.c.decision_json)
        .where(
            (_neurocomment_challenges.c.challenge_hash == challenge_hash)
            & (_neurocomment_challenges.c.outcome == "solved"),
        )
        .order_by(
            _neurocomment_challenges.c.decided_at.desc(),
            _neurocomment_challenges.c.id.desc(),
        )
        .limit(1)
    )
    with _get_engine().connect() as connection:
        row = connection.execute(statement).first()
    if row is None or row[0] is None:
        return None
    return ChallengeDecision.model_validate_json(str(row[0]))


async def lookup_cached_decision(challenge_hash: str) -> ChallengeDecision | None:
    """Reuse a previously solved decision for the same challenge hash (global cache)."""
    return await asyncio.to_thread(_lookup_cached_decision, challenge_hash)


def _evict_cached_decision(challenge_hash: str) -> int:
    statement = delete(_neurocomment_challenges).where(
        (_neurocomment_challenges.c.challenge_hash == challenge_hash)
        & (_neurocomment_challenges.c.outcome == "solved"),
    )
    with _get_engine().begin() as connection:
        result = connection.execute(statement)
    return result.rowcount


async def evict_cached_decision(challenge_hash: str) -> int:
    """Drop the cached solved decision(s) for a hash after a re-challenge proved it wrong.

    A poisoned cache row would otherwise make every future account sharing the same
    challenge click the same wrong button; evicting it forces a fresh LLM decision.
    Returns the number of solved rows removed.
    """
    return await asyncio.to_thread(_evict_cached_decision, challenge_hash)


def _resolve_pending_outcome(account_id: str, channel: str, outcome: str) -> bool:
    # Resolve the latest still-pending row for the pair — the click the engine just
    # verified by attempting a comment. No pending row → no-op, returns False.
    latest_pending = (
        select(_neurocomment_challenges.c.id)
        .where(
            (_neurocomment_challenges.c.account_id == account_id)
            & (_neurocomment_challenges.c.channel == channel)
            & (_neurocomment_challenges.c.outcome == "pending"),
        )
        .order_by(
            _neurocomment_challenges.c.decided_at.desc(),
            _neurocomment_challenges.c.id.desc(),
        )
        .limit(1)
    )
    with _get_engine().begin() as connection:
        target_id = connection.execute(latest_pending).scalar()
        if target_id is None:
            return False
        # The pending-guard makes concurrent resolutions winner-takes-all so the
        # channel failure counter can't be double-counted.
        result = connection.execute(
            update(_neurocomment_challenges)
            .where(
                (_neurocomment_challenges.c.id == target_id)
                & (_neurocomment_challenges.c.outcome == "pending"),
            )
            .values(outcome=outcome, outcome_at=_now_iso()),
        )
    return result.rowcount > 0


async def resolve_pending_outcome(account_id: str, channel: str, outcome: str) -> bool:
    """Resolve the pair's latest pending challenge; ``True`` if one was found + updated."""
    return await asyncio.to_thread(_resolve_pending_outcome, account_id, channel, outcome)


def _count_by_outcome(channels: list[str], since: str) -> ChallengeOutcomeCounts:
    if not channels:
        return ChallengeOutcomeCounts()
    statement = (
        select(_neurocomment_challenges.c.outcome, func.count())
        .where(
            _neurocomment_challenges.c.channel.in_(channels)
            & (_neurocomment_challenges.c.decided_at >= since),
        )
        .group_by(_neurocomment_challenges.c.outcome)
    )
    with _get_engine().connect() as connection:
        rows = connection.execute(statement).all()
    counts = {str(outcome): int(total) for outcome, total in rows}
    return ChallengeOutcomeCounts(
        solved=counts.get("solved", 0),
        failed=counts.get("failed", 0),
        give_up=counts.get("give_up", 0),
        pending=counts.get("pending", 0),
    )


async def count_by_outcome(channels: list[str], since: str) -> ChallengeOutcomeCounts:
    """Header counters: challenge outcomes for ``channels`` with ``decided_at >= since``."""
    return await asyncio.to_thread(_count_by_outcome, channels, since)
