"""Reader rotation for the deletion sweep — split out of ``services.neurocomment._sweep``.

The sweep checks its own comments by re-reading them as a member of the channel's
discussion group, and the only members it has are the accounts that commented there. It
used to read as ``comments[0].account_id`` — whoever the repository happened to return
first, on a query with no ORDER BY — so one kicked account silenced the whole check: a
single live day produced 136 ``RPC: ChannelPrivateError`` lines on one channel and 37 on
another, all from that one account, while whatever those channels deleted went unnoticed.

So the authors are walked instead, and the two verdicts that mean "this account is not in
that chat any more" park the pair with the same sentinel ``_classify`` and ``_outcomes``
write, which hands it to ``_rejoin.review_access_lost`` — a rule that already exists and
already knows how to spend a re-join. Unless EVERY author says it, which is the channel
talking rather than the accounts: parking on that turns one channel gone private into a
whole channel's worth of parked pairs, and 48 hours later into an unlinked channel (see
:func:`_park_kicked_readers`). Own module because ``_sweep`` sits near the aislop file-size
cap; the gateway is still reached through ``_seams.execute_read``, so every patch seam is
unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.db import fetch_readiness, upsert_readiness
from core.logging import log_event
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.telegram_actions import CheckMessagesAlive, CheckMessagesAliveResult
from services.neurocomment import _rejoin, _seams

if TYPE_CHECKING:
    from schemas.neurocomment import CommentRecord, NeurocommentReadiness

# ``execute_read`` collapses every Telethon RPC failure to ``f"RPC: {type(exc).__name__}"``
# (``core.telegram_client._read``), so the class name is the only machine-readable half of
# a kicked read. The other families never take this shape — a flood wait reads
# ``FloodWait(42s)`` and a pool/socket fault ``unavailable: TimeoutError`` — which is what
# keeps them out of the access-loss branch below without a second condition.
_RPC_REASON_PREFIX = "RPC: "
# The same pair ``_outcomes._LOST_ACCESS_ERRORS`` keys the post-side access loss on, and
# for its reason: both mean we were in the chat and are not any more. Nothing else may be
# added here without weighing it against the side effect — parking pulls the pair out of
# service and spends from the pair's re-join budget.
_LOST_ACCESS_ERRORS = frozenset({"ChannelPrivateError", "UserNotParticipantError"})
# How long a channel every reader is out of is left alone. That verdict parks nobody ON
# PURPOSE (:func:`_park_kicked_readers`), so nothing about the next tick can differ: the same
# walk, the same live ``GetFullChannelRequest``, the same WARNING — 288 of each a day, and a
# live pair of channels spent 137 and 27 of them in half a day. An hour keeps a recovery
# noticed inside one, at a 12th of the cost. A constant rather than a setting because there
# is nothing here for an operator to tune: it only spaces out a line they already have, and
# losing it on restart is the right default anyway.
_MUTE_FOR = timedelta(hours=1)
# Channel -> when it may be walked again. Process-local BY DESIGN, and the one piece of this
# module's state that is: a readiness row would park a pair and spend a re-join, which is
# exactly what the all-kicked branch refuses to do, so the back-off has to live where
# ``_rejoin`` cannot mistake it for a verdict about an account.
_MUTED_UNTIL: dict[str, datetime] = {}


def reset_read_mutes() -> None:
    """Forget every muted channel (test seam — same shape as ``_sweep.reset_prune_clock``)."""
    _MUTED_UNTIL.clear()


def _muted(channel: str) -> bool:
    """True while this channel's all-kicked cooldown still holds.

    Reads the clock rather than taking the caller's: ``_sweep._sweep_channel`` threads no
    ``now``, and this deadline is shared with no other pass, so nothing can disagree about
    where a tick falls the way the passes on the sweep's own clock could.
    """
    muted_until = _MUTED_UNTIL.get(channel)
    return muted_until is not None and datetime.now(UTC) < muted_until


def _lost_access_error(exc: BaseException) -> str | None:
    """The Telegram class name when this read failed because we are not in the chat.

    Only a reason the gateway BUILT from an RPC class name qualifies, hence the prefix
    test rather than a bare ``removeprefix``: ``execute_read_many`` also wraps a
    ``ChannelGatewayError`` as ``TelegramReadError(exc.code)`` with no prefix at all, so
    a gateway code that happened to spell one of these names would have parked a pair on
    a refusal that says nothing about membership. No code does today — this keeps it that
    way without anyone having to check the two lists against each other again.
    """
    if not isinstance(exc, TelegramReadError) or not exc.reason.startswith(_RPC_REASON_PREFIX):
        return None
    error_type = exc.reason.removeprefix(_RPC_REASON_PREFIX)
    return error_type if error_type in _LOST_ACCESS_ERRORS else None


def _may_be_in_chat(readiness: NeurocommentReadiness | None) -> bool:
    """True while this pair could still be a member of the channel's discussion group.

    Four states say it cannot, and every one of them is a row another rule owns:

    * ``banned`` — ``bans._mark_banned_and_leave`` marked the pair and walked it out of
      the chat, and that mark is sticky by design;
    * ``human_skipped`` — the operator took the pair out of service (#148) and onboarding
      refuses to re-join it;
    * ``captcha_gave_up`` — ``_captcha_retry._give_up_and_leave`` spent the last re-solve,
      marked the pair terminal and walked it out of the chat, the same shape as the ban;
    * :func:`_rejoin.access_lost` — already parked, the re-join rule owns the retry.

    The first three are exactly the pairs ``access_lost`` cannot cover — none of them can
    satisfy its unjoined-plus-``captcha_passed`` sentinel — so skipping only the parked ones
    never covered them: a banned pair was re-read on every tick for as long as its comments
    stayed in the ``deletion_sweep_lookback_hours`` window — 288 ticks a day into a group that
    had just banned the account and seen it leave — and each failure re-wrote the row into the
    access-loss sentinel, which on a skipped pair silently replaced the operator's mark and
    on a captcha-retired one handed a pair onboarding will NEVER re-join to ``_rejoin``: the
    review stamps an attempt no pass can answer, so ``attempt_owed`` stays true forever.
    No row = never onboarded here, but it commented, so it was in that chat: read with it.
    """
    if readiness is None:
        return True
    return not (
        readiness.banned
        or readiness.human_skipped
        or readiness.captcha_gave_up
        or _rejoin.access_lost(readiness)
    )


def _reader_candidates(comments: list[CommentRecord]) -> list[str]:
    """Every account that commented here, freshest comment first, each named once.

    Freshest first because the most recent author is the one most recently proven able to
    write in the group, so it is the likeliest to still be able to read it; the older ones
    are the fallbacks. The repository read carries no ORDER BY, so this is also the only
    thing that makes the choice a choice rather than a row order.
    """
    freshest_first = sorted(comments, key=lambda comment: comment.created_at, reverse=True)
    return list(dict.fromkeys(comment.account_id for comment in freshest_first))


async def _park_kicked_readers(
    channel: str,
    kicked: list[tuple[str, str]],
    tried: int,
) -> int:
    """Park the readers Telegram said are out of the chat; answer how many were parked.

    Called once the walk is over, never from inside it, because one reader's verdict only
    means what it says after the others have had their turn: ``CheckMessagesAlive`` resolves
    the BROADCAST channel before it ever reaches the discussion group
    (``_read._resolve_linked_group_entity`` → ``GetFullChannelRequest``), so a channel that
    has gone private answers ``ChannelPrivateError`` to every account in turn. Every author
    failing the same way is therefore a fact about the CHANNEL, and parking on it would read
    that fact as a per-account one — leaving the channel with no ready pair, which is
    precisely what ``_rejoin._drop_channel_if_nothing_works`` unlinks a channel for 48 hours
    later. So that case parks nobody and only leaves the log line below — one line per tick
    for as long as it lasts, which is exactly what this failure cost before the rotation
    existed, and the trade is not close: a WARNING an operator can act on is cheaper than a
    live campaign's channel unlinked out from under it. One reader out of several is the real
    kick this walk was written for, and it still parks.

    ``tried == 1`` needs no clause of its own: a lone author IS every author, and it is the
    same call for the same reason. It cannot tell "I was kicked" from "the channel is gone",
    and the two mistakes cost nothing like each other. A false park takes a working pair out
    of service and starts that 48-hour countdown on a channel that may be perfectly fine,
    while a missed one costs one read per tick — and is not missed for long: the pair keeps
    ``ready``, so the channel's next post is attempted with it, and ``_outcomes`` parks it on
    these same two verdicts with proof that it tried to WRITE. That is where every access
    loss came from before this walk existed, so declining here gives up nothing we had.
    """
    if len(kicked) == tried:
        return 0
    for reader, error_type in kicked:
        # Onboarding's hard-join-failure sentinel, field for field the set
        # ``_rejoin.access_lost`` reads: unjoined + captcha_passed is the combination no
        # other path writes, and ready=False stops the pair being selected meanwhile.
        await upsert_readiness(
            reader,
            channel,
            joined=False,
            captcha_passed=True,
            ready=False,
            access_lost_reason=error_type,
        )
    return len(kicked)


async def _close_walk(
    channel: str,
    alive: CheckMessagesAliveResult | None,
    reader: str | None,
    verdict: tuple[int, int, int],
    failure: dict[str, object],
) -> None:
    """Report what the finished walk was worth reporting, and mute the one that will repeat.

    ``verdict`` is ``(tried, kicked, parked)``. Silent when somebody read (nothing failed
    that the caller cannot see) and silent when the walk tried nobody — every author banned,
    skipped, captcha-retired or already parked. That state IS those rows, which the board
    badges and the re-join review acts on every tick, and a channel can sit in it for as long
    as an operator leaves it there: a line per tick would be the five-minute drip this module
    removed.

    The all-kicked verdict is that same drip through the last door left open, and the only
    walk that can be: everybody tried said it is out and :func:`_park_kicked_readers`
    deliberately wrote nothing, so the next tick would find precisely this state, ask
    precisely these accounts and say precisely this line — 288 times a day. It gets the line
    once and then the channel is left alone until ``_MUTE_FOR`` lapses. ``tried`` guards that
    arithmetic as well as the silence: a walk that asked nobody is also 0 kicked of 0, and
    re-stamping on that would push the deadline out on every tick the mute itself silences.
    """
    tried, kicked, parked = verdict
    if alive is not None:
        # Somebody is in that chat after all, so a deadline set earlier must neither go on
        # silencing a later genuine failure nor date it from the wrong hour.
        _MUTED_UNTIL.pop(channel, None)
        return
    if not tried:
        return
    if not parked and kicked == tried:
        _MUTED_UNTIL[channel] = datetime.now(UTC) + _MUTE_FOR
    await log_event(
        "WARNING",
        "neurocomment_sweep_read_failed",
        account_id=reader,
        # Three counts because the rule above turns on the gap between them:
        # ``readers_kicked == readers_tried`` with ``readers_parked`` at 0 is the
        # channel having gone invisible to everyone, which is why nothing was touched
        # (and why nothing will be asked again for an hour); a smaller ``readers_kicked``
        # is the accounts, and every one of those was parked. Without the middle number
        # the line could not tell an all-kicked channel from a walk that ended on a flood
        # wait.
        extra={
            "channel": channel,
            "readers_tried": tried,
            "readers_kicked": kicked,
            "readers_parked": parked,
        }
        | failure,
    )


async def read_alive(
    channel: str,
    comments: list[CommentRecord],
    msg_ids: list[int],
) -> CheckMessagesAliveResult | None:
    """Re-read ``msg_ids`` in ``channel`` as its comment authors in turn; first answer wins.

    ``None`` means nobody could read, and a walk that tried anybody then writes EXACTLY ONE
    ``neurocomment_sweep_read_failed`` line for the channel on this tick — whatever ended
    it, the gateway answering off-contract included, which used to return ``None`` from
    inside the loop and leave no trace at all. A line per failed reader is what made the
    old log unreadable; ``readers_tried`` is what separates "the first account is out" from
    "this channel has nobody left who can check it".

    Only pairs :func:`_may_be_in_chat` still admits are read with — reading as an account
    the group banned is the anti-ban risk this whole domain is built to avoid.

    A walk that ends with every reader tried kicked and nobody parked leaves NOTHING changed
    for the next tick to find, so it mutes the channel for ``_MUTE_FOR`` and returns ``None``
    before the walk — no RPC, no line — until the deadline lapses. Any read that works forgets
    the mute again; the muting itself writes nothing an operator or another rule can see.

    Two verdicts and only two — ``ChannelPrivateError`` and ``UserNotParticipantError`` —
    are Telegram saying that the account which produced them is not in the chat, so the walk
    notes it down and moves on; :func:`_park_kicked_readers` then decides, once the whole
    outcome is in, which of those notes are about accounts and which are about the channel.
    A missing account row moves the walk on too, being our bookkeeping rather than a
    Telegram verdict. Every other failure (flood wait, pool fault, timeout) is about the
    moment rather than the membership, so it parks nobody AND ends the walk for this channel
    on this tick: those faults are usually ours, not this account's, and spending the
    channel's remaining accounts on them would multiply one flood wait into several.

    Nothing here propagates a read failure: the caller is a sweep pass that must survive
    every tick. A repository fault still can, and is caught by the per-channel guard in
    ``_sweep._sweep_once`` — the sweep loop cannot die either way.

    ponytail: a reader Telegram answers with all-ids-gone instead of an error (a public
    group it can still see, having been kicked) would still have its comments stamped
    deleted. The rotation cannot see that; add a reader quorum over the ANSWERS only if the
    canary ever shows it.
    """
    if _muted(channel):
        return None
    tried = 0
    # (reader, verdict) per author Telegram said is out, decided on after the walk: a
    # verdict is only about the account once the other authors have answered.
    kicked: list[tuple[str, str]] = []
    last_reader: str | None = None
    failure: dict[str, object] = {}
    alive: CheckMessagesAliveResult | None = None
    for reader in _reader_candidates(comments):
        if not _may_be_in_chat(await fetch_readiness(reader, channel)):
            # Nobody we could still read with: banned, skipped, or already parked. For the
            # parked one the cost is specific — re-writing the row pushes ``checked_at``
            # past the attempt ``_rejoin`` stamped, which reads as "that attempt was
            # answered" (``_rejoin.attempt_owed``) and cancels a re-join already charged to
            # the budget. One read per candidate, only until one works, on a 5-minute tick.
            continue
        tried += 1
        last_reader = reader
        try:
            result = await _seams.execute_read(
                reader,
                CheckMessagesAlive(channel=channel, message_ids=msg_ids),
            )
        except Exception as exc:  # noqa: BLE001 - a failed read must never abort the sweep.
            # Bounded values only (``test_logevent_extra_bounds``): the gateway's own
            # content-free label, never the third-party prose behind it.
            failure = {"error_type": type(exc).__name__}
            if isinstance(exc, TelegramReadError):
                failure |= {"reason": exc.reason, "kind": exc.kind}
            if isinstance(exc, TelegramAccountNotFoundError):
                # Raised BEFORE the gateway's own try block, so it is not a
                # ``TelegramReadError`` and used to end the walk on the first candidate —
                # the one-account-silences-the-channel failure this module exists to fix,
                # arriving through another door. It is a fact about OUR fleet (the account
                # row is gone), not about this chat and not about our pool, so it parks
                # nobody and the next author still gets its turn.
                continue
            error_type = _lost_access_error(exc)
            if error_type is None:
                break
            kicked.append((reader, error_type))
        else:
            if isinstance(result, CheckMessagesAliveResult):
                # ``break``, not ``return``: an author kicked EARLIER in this walk is parked
                # by the decision below, and returning from here would skip it — the very
                # case (one account out, the next one reads fine) the rotation exists for.
                alive = result
                break
            # Anything else is the typed gateway breaking its contract; the next account
            # would not answer differently, so the walk ends here — but it must not end
            # SILENTLY, which is what returning ``None`` from inside the loop did: the
            # channel simply stopped being checked, with nothing in the journal to say so.
            # ``error_type`` carries the class we got instead, which is the whole diagnosis.
            failure = {"error_type": type(result).__name__, "reason": "unexpected_result"}
            break
    parked = await _park_kicked_readers(channel, kicked, tried)
    await _close_walk(channel, alive, last_reader, (tried, len(kicked), parked), failure)
    return alive
