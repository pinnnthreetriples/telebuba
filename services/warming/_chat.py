"""One inter-account dialogue turn per cycle — who talks to whom, and the send.

The line itself comes from :mod:`services.warming._chat_text`. Telegram and
randomness are reached through :mod:`services.warming._seams` so tests patch
those seams in one place.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    count_pair_messages_since,
    list_accounts,
    mark_message_replied,
    mark_message_unreplied,
    oldest_unreplied_for,
    pair_key,
    partners_awaiting_our_reply,
    record_dialogue_message,
    try_claim_message_reply,
)
from core.logging import log_event
from schemas.telegram_actions import ActionResult, MarkDirectMessageRead, SendDirectMessage
from services.content import release_sent_text
from services.dialogues import get_partners
from services.warming import _seams
from services.warming._chat_text import _opener_text, _reply_text
from services.warming._fleet import _stable_fraction
from services.warming.pacing import _HALT_STATUSES, _classify_flood, persona_dm_probability

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.dialogues import DialogueMessage
    from schemas.warming import WarmingCycleRequest, WarmingSettingsSecret
    from services.warming._cycle import _ChannelTally

# The gateway's class name for "this partner is permanently unaddressable" —
# no phone to look up, or phone-lookup privacy hides them.
_PEER_UNRESOLVED = "DmPeerUnresolvedError"


@dataclasses.dataclass
class ChatResult:
    messages_sent: int = 0
    failures: int = 0
    attempted_actions: int = 0
    flood_result: ActionResult | None = None
    last_failed_action: str | None = None


def _account_typing_wpm(account_id: str) -> int:
    """Stable-but-distinct typing tempo for an account, uniform in [min, max].

    Reuses the salted, process-stable fleet hash so each account keeps the same
    WPM across cycles while the fleet spreads across the range.
    """
    warm = settings.warming
    span = warm.typing_wpm_max - warm.typing_wpm_min + 1
    return warm.typing_wpm_min + int(_stable_fraction(f"wpm:{account_id}") * span)


async def _maybe_inter_account_chat(
    sender_id: str,
    secret: WarmingSettingsSecret,
) -> ChatResult:
    """Advance one dialogue turn for ``sender_id`` with one of its partners.

    Replies to the longest-waiting unanswered message from a partner; otherwise
    opens a new conversation with an eligible partner. Returns structured result.
    """
    partners = (await get_partners(sender_id)).partners
    if not partners:
        return ChatResult()
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}

    incoming = await oldest_unreplied_for(sender_id)
    while incoming is not None and incoming.from_account not in partners:
        # Orphan: the head of the queue is from a NON-partner (e.g. an ex-partner
        # after a reshuffle). Drain the whole run of them in one pass rather than
        # one per cycle: the inbox is FIFO, so N orphans sitting in front of a
        # real partner message would otherwise cost N cycles — each one burning
        # its dialogue turn on bookkeeping — before that partner got an answer.
        # The loop terminates because every iteration flips a distinct row to
        # replied=1 and the query only ever returns replied=0 rows.
        await mark_message_replied(incoming.id)
        incoming = await oldest_unreplied_for(sender_id)
    if incoming is not None:
        return await _reply_to_partner(sender_id, incoming, secret, accounts)
    return await _open_with_partner(sender_id, partners, secret, accounts)


def _should_chat(
    data: WarmingCycleRequest,
    secret: WarmingSettingsSecret,
    tally: _ChannelTally,
    *,
    dm_allowed: bool,
    can_attempt: bool,
) -> bool:
    """All gates for an inter-account DM this cycle.

    П11: ``dm_allowed`` is the loop's trust+readiness-aware permission (age-only
    for direct callers). The persona roll is last so it draws only once every
    prior gate passed — it decides *how often* to chat, not whether it may.
    """
    return (
        can_attempt
        and not tally.flooded
        and not tally.peer_flooded
        and dm_allowed
        and secret.inter_account_chat
        and bool(secret.gemini_api_key)
        and _seams.rng.random() < persona_dm_probability(data.activity_persona)
    )


async def _run_chat_step(
    data: WarmingCycleRequest,
    secret: WarmingSettingsSecret,
    tally: _ChannelTally,
    *,
    dm_allowed: bool,
    can_attempt: bool,
) -> int:
    """Maybe start/continue an inter-account DM; return messages_sent.

    Any flood is folded into ``tally``.
    """
    if not _should_chat(data, secret, tally, dm_allowed=dm_allowed, can_attempt=can_attempt):
        return 0
    # A turn bills at most one action (one dialogue turn per cycle, one send in
    # it), so book that action BEFORE the turn runs and give it back if the turn
    # sent nothing. Folding only on return would leave the loop's #208 reconcile
    # blind to a DM that really left the process: between the send RPC returning
    # and this line sit the dialogue bookkeeping writes and the event log, and a
    # cancellation at any of them must not under-count the spend. Erring one
    # action high for the length of a turn fails closed, which is the direction
    # the daily cap needs.
    tally.attempts += 1
    chat_result = await _maybe_inter_account_chat(data.account_id, secret)
    tally.attempts += chat_result.attempted_actions - 1
    tally.failures += chat_result.failures
    if chat_result.last_failed_action:
        tally.last_failed_action = chat_result.last_failed_action
    if chat_result.flood_result:
        if chat_result.flood_result.status == "peer_flood":
            tally.peer_flooded = True
        else:
            tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(
                chat_result.flood_result,
            )
        tally.last_failed_action = chat_result.last_failed_action or "send_dm"
    return chat_result.messages_sent


async def _reply_to_partner(  # noqa: PLR0911
    sender_id: str,
    incoming: DialogueMessage,
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> ChatResult:
    target = accounts.get(incoming.from_account)
    if target is None or target.user_id is None:
        await mark_message_replied(incoming.id)
        return ChatResult()
    if await _conversation_faded(sender_id, incoming.from_account):
        # Long enough — let it fade rather than ping-pong forever. Marking the
        # message replied ends the thread; a new one may start after the window.
        await mark_message_replied(incoming.id)
        await log_event(
            "INFO",
            "warming_dialogue_faded",
            account_id=sender_id,
            extra={"with": incoming.from_account},
        )
        return ChatResult()
    # Read receipt + read-to-reply delay: a real user opens and reads the DM
    # before answering, so mark it read then pause briefly. A failed read-ack is
    # not a dialogue failure — proceed to reply regardless.
    read = await _seams.execute(
        sender_id,
        MarkDirectMessageRead(
            user_id=target.user_id,
            peer_phone=target.phone,
        ),
    )
    if read.status != "ok":
        # The read-ack resolves the same peer the send would, so an unresolvable
        # partner is already decided here — bail before spending a second lookup
        # and a Gemini generation on a reply that cannot be delivered.
        if read.error_type == _PEER_UNRESOLVED:
            return await _drop_unresolvable_reply(sender_id, incoming)
        await log_event(
            "INFO",
            "warming_dialogue_read_ack_skipped",
            account_id=sender_id,
            extra={"with": incoming.from_account},
        )
    warm = settings.warming
    delay = _seams.rng.uniform(
        warm.dm_read_reply_delay_min_seconds, warm.dm_read_reply_delay_max_seconds
    )
    await _seams.sleep(delay)
    gen = await _reply_text(sender_id, secret, incoming)
    if gen.text is None:
        return ChatResult(failures=1, last_failed_action=gen.failure_reason)
    text = gen.text
    # Atomic claim before send: collapses ``oldest_unreplied_for`` + ``mark``
    # into one UPDATE WHERE replied=0 so two parallel cycles cannot both
    # answer the same incoming message. F6: if the send fails *transiently*
    # (flood / halt), we release the claim so the inbox keeps the message for
    # the next cycle instead of losing it forever.
    if not await try_claim_message_reply(incoming.id):
        # The text reservation in _generate_chat_text would otherwise lock
        # this exact text out of the dedup window for nothing.
        await release_sent_text(text)
        return ChatResult()
    # The text was already reserved by `try_reserve_sent` inside `_generate_chat_text`.
    result = await _seams.execute(
        sender_id,
        SendDirectMessage(
            user_id=target.user_id,
            text=text,
            typing_wpm=_account_typing_wpm(sender_id),
            peer_phone=target.phone,
        ),
    )

    if result.status in _HALT_STATUSES:
        await mark_message_unreplied(incoming.id)
        # P2.6: drop the reservation so the next retry of an identical reply
        # isn't shadowed for the entire dedup window.
        await release_sent_text(text)
        return ChatResult(attempted_actions=1, flood_result=result, last_failed_action="send_dm")
    if result.status != "ok":
        await release_sent_text(text)
        if result.error_type == _PEER_UNRESOLVED:
            return await _drop_unresolvable_reply(sender_id, incoming)
        # Keep the claim — deliberately narrowing F6's "never lose a message" to
        # the transient failures above. A generic send failure is peer-specific
        # (blocked, privacy-restricted, deactivated) and repeats identically, and
        # since the inbox went FIFO a re-armed row returns to the head every
        # cycle: it would pin the queue forever, starving every other partner
        # while burning a read-ack, a Gemini generation and a doomed send each
        # time. The cost of consuming it is one unanswered synthetic line — the
        # pair is not dead, the opener can start a fresh thread next cycle.
        return ChatResult(failures=1, attempted_actions=1, last_failed_action="send_dm")
    # Chain: record our reply as a new pending message so the partner can answer
    # next cycle — this is what turns a single round-trip into a conversation.
    await record_dialogue_message(sender_id, incoming.from_account, text)
    await log_event(
        "INFO",
        "warming_dialogue_reply",
        account_id=sender_id,
        extra={"to": incoming.from_account},
    )
    return ChatResult(messages_sent=1, attempted_actions=1)


async def _skip_unresolvable_peer(sender_id: str, partner_id: str) -> ChatResult:
    """A partner Telegram will never hand us — skip the turn without failing the cycle.

    No usable phone, or their phone-lookup privacy hides them: either way the
    pair is permanently undeliverable. Counting it as a failure would park an
    otherwise healthy sender in the terminal ``error`` state.

    The turn still spent RPCs, so it is billed as an attempt: that keeps the
    phone lookup — the most abuse-monitored call the fleet makes — inside the
    daily cap instead of running off-budget beside it.

    ponytail: no negative cache. The reply path consumes the message so a stuck
    pair stops re-arming, but the opener still re-picks this partner and pays
    the full cold-resolve every cycle — ``users.GetUsers`` for the cache miss,
    then ``contacts.resolvePhone``. Cycles are hours apart and the cap now
    absorbs it, so that is a handful of calls a day. Give the pair a cooldown if
    the fleet ever carries more than a couple of these.
    """
    await log_event(
        "WARNING",
        "warming_dialogue_peer_unresolved",
        account_id=sender_id,
        extra={"with": partner_id},
    )
    return ChatResult(attempted_actions=1)


async def _drop_unresolvable_reply(sender_id: str, incoming: DialogueMessage) -> ChatResult:
    """Skip the pair and consume the message that would otherwise re-arm it.

    Leaving it pending would resurface the same undeliverable partner every
    cycle forever — ``_conversation_faded`` needs 12 turns to let go and a stuck
    pair never gets past one. Marking it replied ends the thread, exactly as an
    unknown partner already does at the top of ``_reply_to_partner``.
    """
    await mark_message_replied(incoming.id)
    return await _skip_unresolvable_peer(sender_id, incoming.from_account)


def _conversation_window_start() -> str:
    """ISO start of the current conversation window (``dialogue_conversation_window_hours``)."""
    hours = settings.warming.dialogue_conversation_window_hours
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def _conversation_faded(account_a: str, account_b: str) -> bool:
    """True once a pair has exchanged ``dialogue_max_turns`` within the window."""
    count = await count_pair_messages_since(
        pair_key(account_a, account_b),
        _conversation_window_start(),
    )
    return count >= settings.warming.dialogue_max_turns


async def _open_with_partner(  # noqa: PLR0911 - one early exit per skip/failure reason
    sender_id: str,
    partners: list[str],
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> ChatResult:
    # F8: when paired loops fire at the same instant, both sides would see an
    # empty inbox and both open the conversation, producing crossing DMs.
    # Restrict the opener role to the lexicographically smaller account_id;
    # the other side waits and replies on its next cycle.
    candidates = [
        accounts[partner]
        for partner in partners
        if accounts.get(partner) is not None
        and accounts[partner].user_id is not None
        and sender_id < partner
    ]
    # Partners who have not answered our last DM yet. Without this the opener
    # re-picks the same partner every cycle — our own sent row carries
    # to_account=partner so it never shows up in our own inbox lookup — and the
    # only brake is the 12-message fade below: up to a dozen consecutive
    # one-sided DMs, the exact spam signature warming exists to avoid.
    # Deliberately bounded by the conversation window: a pending message older
    # than that is a dead thread, and a permanent block would silence the pair
    # forever and, fleet-wide, eventually stop the opener firing at all.
    awaiting = await partners_awaiting_our_reply(
        sender_id,
        [account.account_id for account in candidates],
        _conversation_window_start(),
    )
    # Skip partners this pair has already exhausted within the window. The reply
    # path fades (sends nothing) once dialogue_max_turns is hit; an opener that
    # ignored the fade would keep sending fresh one-sided DMs the partner never
    # answers — the spam signature warming avoids. Let the pair rest until the
    # window rolls off, mirroring _reply_to_partner's _conversation_faded gate.
    eligible = [
        account
        for account in candidates
        if account.account_id not in awaiting
        and not await _conversation_faded(sender_id, account.account_id)
    ]
    if not eligible:
        return ChatResult()
    target = _seams.rng.choice(eligible)
    if target.user_id is None:
        return ChatResult()
    gen = await _opener_text(sender_id, secret, target.account_id)
    if gen.text is None:
        return ChatResult(failures=1, last_failed_action=gen.failure_reason)
    text = gen.text
    # The text was already reserved by `try_reserve_sent` inside `_generate_chat_text`.
    result = await _seams.execute(
        sender_id,
        SendDirectMessage(
            user_id=target.user_id,
            text=text,
            typing_wpm=_account_typing_wpm(sender_id),
            peer_phone=target.phone,
        ),
    )

    if result.status in _HALT_STATUSES:
        # P2.6: drop the reservation so the next opener retry isn't shadowed.
        await release_sent_text(text)
        return ChatResult(attempted_actions=1, flood_result=result, last_failed_action="send_dm")
    if result.status != "ok":
        await release_sent_text(text)
        if result.error_type == _PEER_UNRESOLVED:
            return await _skip_unresolvable_peer(sender_id, target.account_id)
        return ChatResult(failures=1, attempted_actions=1, last_failed_action="send_dm")

    await record_dialogue_message(sender_id, target.account_id, text)
    await log_event(
        "INFO",
        "warming_dialogue_opened",
        account_id=sender_id,
        extra={"to": target.account_id},
    )
    return ChatResult(messages_sent=1, attempted_actions=1)
