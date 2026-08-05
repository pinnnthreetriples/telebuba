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
already knows how to spend a re-join. Own module because ``_sweep`` sits near the aislop
file-size cap; the gateway is still reached through ``_seams.execute_read``, so every
patch seam is unchanged.
"""

from __future__ import annotations

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

    Three states say it cannot, and every one of them is a row another rule owns:

    * ``banned`` — ``bans._mark_banned_and_leave`` marked the pair and walked it out of
      the chat, and that mark is sticky by design;
    * ``human_skipped`` — the operator took the pair out of service (#148) and onboarding
      refuses to re-join it;
    * :func:`_rejoin.access_lost` — already parked, the re-join rule owns the retry.

    The first two are exactly the pairs ``access_lost`` excludes BY CONSTRUCTION (its own
    docstring says why), so skipping only the parked ones never covered them: a banned
    pair was re-read on every tick for as long as its comments stayed in the
    ``deletion_sweep_lookback_hours`` window — 288 ticks a day into a group that had just
    banned the account and seen it leave — and each failure re-wrote the row into the
    access-loss sentinel, which on a skipped pair silently replaced the operator's mark.
    No row = never onboarded here, but it commented, so it was in that chat: read with it.
    """
    if readiness is None:
        return True
    return not (readiness.banned or readiness.human_skipped or _rejoin.access_lost(readiness))


def _reader_candidates(comments: list[CommentRecord]) -> list[str]:
    """Every account that commented here, freshest comment first, each named once.

    Freshest first because the most recent author is the one most recently proven able to
    write in the group, so it is the likeliest to still be able to read it; the older ones
    are the fallbacks. The repository read carries no ORDER BY, so this is also the only
    thing that makes the choice a choice rather than a row order.
    """
    freshest_first = sorted(comments, key=lambda comment: comment.created_at, reverse=True)
    return list(dict.fromkeys(comment.account_id for comment in freshest_first))


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

    Two verdicts and only two — ``ChannelPrivateError`` and ``UserNotParticipantError`` —
    park the pair that produced them, because they are Telegram saying that account is not
    in the chat; the walk then moves on. So does a missing account row, which is our
    bookkeeping rather than a Telegram verdict. Every other failure (flood wait, pool
    fault, timeout) is about the moment rather than the membership, so it parks nobody AND
    ends the walk for this channel on this tick: those faults are usually ours, not this
    account's, and spending the channel's remaining accounts on them would multiply one
    flood wait into several.

    Nothing here propagates a read failure: the caller is a sweep pass that must survive
    every tick. A repository fault still can, and is caught by the per-channel guard in
    ``_sweep._sweep_once`` — the sweep loop cannot die either way.

    ponytail: a reader Telegram answers with all-ids-gone instead of an error (a public
    group it can still see, having been kicked) would still trip a false back-off. The
    rotation cannot see that; add a reader quorum only if the canary ever shows it.
    """
    tried = 0
    parked = 0
    last_reader: str | None = None
    failure: dict[str, object] = {}
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
            parked += 1
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
        else:
            if isinstance(result, CheckMessagesAliveResult):
                return result
            # Anything else is the typed gateway breaking its contract; the next account
            # would not answer differently, so the walk ends here — but it must not end
            # SILENTLY, which is what returning ``None`` from inside the loop did: the
            # channel simply stopped being checked, with nothing in the journal to say so.
            # ``error_type`` carries the class we got instead, which is the whole diagnosis.
            failure = {"error_type": type(result).__name__, "reason": "unexpected_result"}
            break
    if tried:
        # Silent when the walk tried nobody — every author banned, skipped or already
        # parked. That state IS those rows, which the board badges and the re-join review
        # acts on every tick, and a channel can sit in it for as long as an operator leaves
        # it there: a line per tick would be the five-minute drip this module removed.
        await log_event(
            "WARNING",
            "neurocomment_sweep_read_failed",
            account_id=last_reader,
            # ``readers_parked`` is what keeps the line from blaming the accounts for a
            # dead channel. ``CheckMessagesAlive`` starts at the BROADCAST channel
            # (``_read._resolve_linked_group_entity`` → ``GetFullChannelRequest``), so a
            # ``ChannelPrivateError`` can be the channel going invisible to everyone
            # rather than this account losing the discussion group — and when that
            # happens every author parks on the same tick. ``readers_parked ==
            # readers_tried`` is that signature; one parked out of several is a real kick.
            extra={"channel": channel, "readers_tried": tried, "readers_parked": parked} | failure,
        )
    return None
