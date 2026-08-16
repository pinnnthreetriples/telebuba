"""What the gateway's answers MEAN to a campaign — joins, resolves, refusals.

The Telegram executor never raises: it classifies and returns. That is the right
contract, and it is also the trap this module exists to close. Four of its answers
look like nothing at all to a caller that only reads ``error_type``:

* The rate-limit family (``flood_wait`` / ``premium_wait`` / ``peer_flood`` /
  ``slow_mode_wait``) is reported as a STATUS and carries no ``error_type`` whatsoever.
  Telethon's auto-sleep is off by design (``flood_sleep_threshold=0``), so nothing
  else pauses either: a caller switching on ``error_type`` sees an empty field,
  files the step as an ordinary failure, and keeps posting after Telegram said stop.
  The split below is this module's own — ``services.warming.pacing`` halts on all
  four of them, because a warming pass holds one channel at a time and has nothing
  to skip TO. Here the question is scope: three are limits on the ACCOUNT and stop
  it for the whole run, and only slow mode belongs to the CHAT.
* A join that queued an approval request answers ``status="failed"`` deliberately,
  and a join into a chat we are already in answers ``already_participant`` rather
  than ``ok``. Reading the status alone therefore fails a success and succeeds a
  non-membership — the second of which would play a whole dialogue into a chat the
  account never entered.
* A send into a chat this account never joined raises ``ValueError`` inside
  Telethon (the session entity cache has no such peer), which the executor folds
  into a generic failure whose ``error_type`` is the bare ``"ValueError"``.
* A deactivated, frozen or logged-out account never arrives as its own error class.
  ``UserDeactivatedBanError`` is an ``UnauthorizedError``, so the gateway catches it
  with the whole dead-session family and re-raises ``ProfileGatewayError`` — which
  is the only thing ``error_type`` then says. The answer survives in
  ``error_message``, which carries the stable code, so that is what is keyed on.

Bans get the same treatment for the same reason: only ``UserBannedInChannelError``
(a 400 about the caller, and the one ban that does reach us as itself) and those
dead-session codes are about the ACCOUNT. ``ChatWriteForbiddenError`` and its
siblings are properties of the CHAT, so substituting a reserve account there spends
an account to hit the identical wall.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

from core.config import settings
from core.logging import log_event
from core.repositories import neuroshilling as repository
from core.repositories.neurocomment import count_account_joins_since, record_join
from core.telegram_client import UNCONFIRMED_ERROR_TYPE, TelegramReadError
from schemas.telegram_actions import JoinChannel, ResolveChat, ResolveChatResult
from services import pacing
from services.neuroshilling import _seams

if TYPE_CHECKING:
    from schemas.neuroshilling import NeuroshillingPresenceState
    from schemas.telegram_actions import ActionResult

# The account is rate-limited: it stops for the rest of the run. ``peer_flood`` is a
# 400 with NO timer — a state rather than a delay — so it gets the same verdict and
# not a wait. ``premium_wait`` is here and not with the chat waits because
# ``FloodPremiumWaitError`` is a 420 counted against the ACCOUNT: moving to the next
# chat would spend the very budget Telegram just refused.
_HALT_STATUSES: Final = frozenset({"flood_wait", "peer_flood", "premium_wait"})
# The CHAT is rate-limited. Another account of the same role hits the same wall a
# moment later, so the step is skipped and nobody is penalised.
_CHAT_WAIT_STATUSES: Final = frozenset({"slow_mode_wait"})
# Telegram says we are in, however it phrases it.
_INSIDE_STATUSES: Final = frozenset({"ok", "already_participant"})

# The account is not merely refused, it cannot act at all — until the operator
# re-logs it in, or ever. Keyed on the MESSAGE because every member of the family is
# an ``UnauthorizedError`` (or a frozen-method 420) the gateway has already folded
# into a ``ProfileGatewayError``: the class name is the wrapper's and says nothing,
# while the stable code rides ``error_message`` verbatim.
_ACCOUNT_DEAD_MESSAGES: Final = frozenset(
    {"account_deactivated", "account_frozen", "session_dead"},
)

# The account is at Telegram's ~500-chat ceiling: nothing to do with this target.
_ACCOUNT_FULL_ERROR: Final = "ChannelsTooMuchError"
_JOIN_REQUEST_ERROR: Final = "InviteRequestSentError"

# Peer shapes a staged multi-account dialogue cannot run in. Basic groups and private
# chats share ONE message-id sequence PER USER, so the id account A must reply to is
# not the id account B was given, and the chain misfires without ever erroring.
_UNUSABLE_KINDS: Final = frozenset({"basic_group", "user"})

SendVerdict = Literal[
    "sent",
    "halt",
    "chat_wait",
    "account_dead",
    "account_banned",
    "chat_blocked",
    "chat_unavailable",
    "not_member",
    # Already on the wire when the connection died: Telegram may well have applied
    # it, so it must never be repeated.
    "unconfirmed",
    "failed",
]

# One refused write, read off the class name the gateway reports. A table rather than
# a ladder of ``if``s because the GROUPING is the whole content: who owns the refusal
# is what decides whether substituting a reserve account can possibly help.
_ERROR_VERDICTS: Final[dict[str, SendVerdict]] = {
    UNCONFIRMED_ERROR_TYPE: "unconfirmed",
    # A 400 about the caller in this chat, and so the one ban that survives as its own
    # class; the rest reach us as the dead-session codes above.
    "UserBannedInChannelError": "account_banned",
    # Refusals that belong to the CHAT: a substitute account meets them identically,
    # so they must never trigger a replacement.
    "ChatWriteForbiddenError": "chat_blocked",
    "ChatSendPlainForbiddenError": "chat_blocked",
    "ChatSendMediaForbiddenError": "chat_blocked",
    "ChatRestrictedError": "chat_blocked",
    "ChatGuestSendForbiddenError": "chat_blocked",
    # Private — or we are banned from it. Telethon's CHANNEL_PRIVATE text covers both
    # and nothing here can tell them apart, so it gets a verdict of its own instead of
    # ``not_member``, whose remedy is a re-join that would loop against the ban.
    "ChannelPrivateError": "chat_unavailable",
    # Not a member — after the per-account resolve rule, the most common failure there
    # is. ``ValueError`` is in the table because that is how an unknown peer reaches
    # us: the session entity cache is per account, so an id one account resolved is
    # simply absent from another's. Keying on that bare class name is safe, because a
    # mistake of OURS — a typo'd kwarg — raises ``TypeError`` instead.
    "UserNotParticipantError": "not_member",
    "ValueError": "not_member",
}

# Stored answers that make another join pointless. ``refused`` is deliberately not
# one of them: it is the pair's verdict on ONE attempt (an expired invite, a chat
# that said no), and the next pass is entitled to try again. These four are either
# already true, already queued, or a verdict on the ACCOUNT that another join would
# only make worse.
_SETTLED_STATES: Final = frozenset({"joined", "pending_approval", "flooded", "retired"})
# Account-wide verdicts, in the sense ``retire_account_presence`` writes them.
_ACCOUNT_HALTED: Final = frozenset({"flooded", "retired"})


def classify_send(result: ActionResult) -> SendVerdict:
    """What one write outcome means for the account, the chat and the step."""
    if result.status in _INSIDE_STATUSES:
        return "sent"
    if result.status in _HALT_STATUSES:
        return "halt"
    if result.status in _CHAT_WAIT_STATUSES:
        return "chat_wait"
    # Before the table and not in it: these arrive under one wrapper class name.
    if result.error_message in _ACCOUNT_DEAD_MESSAGES:
        return "account_dead"
    return _ERROR_VERDICTS.get(result.error_type or "", "failed")


def classify_join(result: ActionResult) -> NeuroshillingPresenceState:
    """Map a join outcome onto the pair's presence state.

    Keyed on ``error_type`` rather than on ``status`` wherever the two disagree,
    because the gateway spends ``status`` on transport and ``error_type`` on
    meaning: ``already_participant`` is a success with a non-``ok`` status, and a
    queued approval request is a ``failed`` whose whole content is its error class.
    """
    if result.status in _INSIDE_STATUSES:
        return "joined"
    if result.status in _HALT_STATUSES:
        return "flooded"
    if result.error_type == _JOIN_REQUEST_ERROR:
        return "pending_approval"
    if result.error_type == _ACCOUNT_FULL_ERROR:
        return "retired"
    # A chat-scoped wait and an infrastructure failure both leave the question open;
    # the pair stays pending so the next pass retries it rather than writing it off.
    if result.status in _CHAT_WAIT_STATUSES or result.status == "unavailable":
        return "pending"
    return "refused"


def _join_gap_seconds() -> float:
    """One jittered join spacing, drawn from the same range neurocomment uses.

    Handed to the pacer per call, so its fixed-interval gate becomes a scattered
    pause and every join by this account is serialised in time however many targets
    are in flight — which is what a separate semaphore would otherwise be for.
    """
    limits = settings.neuroshilling
    return pacing.human_delay(
        limits.join_delay_min_seconds,
        limits.join_delay_max_seconds,
        rng=_seams.rng,
        mu=limits.delay_lognorm_mu,
        sigma=limits.delay_lognorm_sigma,
    )


async def at_join_cap(account_id: str) -> bool:
    """True when ``account_id`` has spent its rolling-24h join budget (0 = no cap).

    Counted out of ``neurocomment_join_log``, the SAME table neurocomment gates on,
    and that is deliberate: Telegram freezes an account somewhere north of 20-50
    joins a day and does not care which of our features spent them. A private
    counter would have let one account join forty times with both features certain
    they had stayed under twenty. The price is that neurocomment reaches its own cap
    sooner when a campaign is running, which is the point rather than a side effect.
    """
    cap = settings.neuroshilling.max_joins_per_account_per_day
    if cap <= 0:
        return False
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    return await count_account_joins_since(account_id, since) >= cap


async def _daily_cap_reached(account_id: str, target: str) -> NeuroshillingPresenceState:
    """Log the refusal and leave the pair as it was.

    ``pending`` without touching the stored row: nothing was learnt about the pair,
    and overwriting a previous refusal with "pending" would erase the only record of
    why it failed.
    """
    await log_event(
        "WARNING",
        "neuroshilling_join_daily_cap",
        account_id=account_id,
        extra={"target": target},
    )
    return "pending"


async def join_target(
    campaign_id: str,
    account_id: str,
    target: str,
) -> NeuroshillingPresenceState:
    """Get one account into one target, and record where that left the pair.

    The stored presence is READ first, which is what the table is for: a pair already
    inside is not re-joined after a restart, and an account halted account-wide does
    not walk on to the next target as if nothing had happened.
    """
    settled = await repository.fetch_presence_state(campaign_id, account_id, target)
    if settled is not None and settled in _SETTLED_STATES:
        return settled
    if await at_join_cap(account_id):
        return await _daily_cap_reached(account_id, target)
    await pacing.await_send_slot(f"join:{account_id}", _join_gap_seconds())
    # The check that counts, and the reason there are two: the pacer is a queue, not
    # a mutex over the cap. Every join of this account waiting in it had passed the
    # check above before any of them recorded anything, so forty targets meant forty
    # joins — spaced, and uncapped. Inside the granted slot the joins that went first
    # have been charged, so this one reads a true count.
    if await at_join_cap(account_id):
        return await _daily_cap_reached(account_id, target)
    result = await _seams.execute(account_id, JoinChannel(channel=target))
    state = classify_join(result)
    if result.status == "ok" or state == "pending_approval":
        # A real join and a queued REQUEST are both charged: Telegram rate-limits the
        # requests too, and a spray of them at gated chats is a recognised freeze
        # trigger. An ``already_participant`` no-op is charged to neither, or the
        # counter would pin near the cap and starve the joins that matter — the same
        # rule the neurocomment listener pass applies to the same table.
        await record_join(account_id)
    await repository.record_presence(
        campaign_id,
        account_id,
        target,
        state,
        error_type=result.error_type,
    )
    if state in _ACCOUNT_HALTED:
        # Both verdicts are about the ACCOUNT, so they apply to every target it was
        # going to play — and they are persisted, not held in a run-local halt set,
        # because the gate at the top of this function is what reads them back.
        await repository.retire_account_presence(account_id, state)
    if state != "joined":
        await log_event(
            "WARNING",
            "neuroshilling_join_blocked",
            account_id=account_id,
            extra={"target": target, "state": state, "status": result.status}
            | ({"error_type": result.error_type} if result.error_type else {}),
        )
    return state


async def record_send_verdict(
    campaign_id: str,
    account_id: str,
    target: str,
    result: ActionResult,
) -> SendVerdict:
    """Classify one write AND persist what it said about the account.

    A flood that arrives mid-dialogue is the same account-wide verdict a flood on the
    join is, and it was the only one of the two nothing wrote down: the run halted,
    the process restarted, and the account resumed posting inside its own flood
    window with a presence table that still said ``joined``.
    """
    verdict = classify_send(result)
    if verdict == "halt":
        await repository.record_presence(
            campaign_id,
            account_id,
            target,
            "flooded",
            error_type=result.error_type,
        )
        await repository.retire_account_presence(account_id, "flooded")
    return verdict


async def resolve_target(
    campaign_id: str,
    account_id: str,
    target: str,
) -> ResolveChatResult | None:
    """This account's own chat id for ``target``, or ``None`` if it cannot be used.

    ``None`` is not one verdict but three, and only the last two are the pair's own
    fault: the read hit a limit (nothing is written — a retry is exactly what is
    wanted), the account cannot reach the chat, or the chat is a basic group /
    private chat, whose message ids are not shared between accounts.
    """
    try:
        resolved = await _seams.execute_read(account_id, ResolveChat(target=target))
    except TelegramReadError as exc:
        await _record_read_failure(campaign_id, account_id, target, exc)
        return None
    if not isinstance(resolved, ResolveChatResult):  # pragma: no cover - union is exhaustive
        message = f"resolve_chat answered {type(resolved).__name__}"
        raise TypeError(message)
    if resolved.kind in _UNUSABLE_KINDS:
        await repository.record_presence(
            campaign_id,
            account_id,
            target,
            "refused",
            error_type="target_is_basic_group",
        )
        await log_event(
            "WARNING",
            "neuroshilling_target_unusable",
            account_id=account_id,
            extra={"target": target, "kind": resolved.kind},
        )
        return None
    return resolved


async def _record_read_failure(
    campaign_id: str,
    account_id: str,
    target: str,
    exc: TelegramReadError,
) -> None:
    """Write down a failed resolve — as what it actually was.

    ``refused`` is PERMANENT here: it is excluded from the account-wide retirement
    sweep, so nothing ever clears it and the pair is written off for the run. A flood
    or a dead socket says nothing about the pair, so recording one as a refusal
    retired a target over a wait that was already over — which is the distinction
    :func:`classify_join` makes on the join and this path used to throw away.
    """
    if exc.kind == "flood_wait":
        await repository.record_presence(
            campaign_id,
            account_id,
            target,
            "flooded",
            error_type=exc.reason,
        )
        await repository.retire_account_presence(account_id, "flooded")
    elif exc.kind == "other":
        await repository.record_presence(
            campaign_id,
            account_id,
            target,
            "refused",
            error_type=exc.reason,
        )
    await log_event(
        "WARNING",
        "neuroshilling_resolve_failed",
        account_id=account_id,
        extra={"target": target, "error_type": exc.reason, "kind": exc.kind},
    )
