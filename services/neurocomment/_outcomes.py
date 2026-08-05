"""What a post attempt MEANT: the outcome ladder and the state writes it owns.

Split from ``_generate`` for the file-size budget, along the seam that was already
there — ``_generate`` decides what to say and when to say it, this module decides what
the answer costs. One error family per branch, each with the state write that stops the
pair (or the channel) looping on the same refusal: cooldown, sticky ban, the
hard-join-failure sentinel, the solver-clearable gate, and a safe default for everything
Telethon leaves unmapped.

The in-flight registry lives here too, because the outcome path is what retires an
entry; ``_generate`` imports it back, so ``_generate.<name>`` (and through it
``engine.<name>``) still resolves for every existing call site and test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    clear_unconfirmed_bans,
    mark_comment_failed,
    mark_comment_posted,
    record_comment_msg_id,
    release_claim,
    resolve_pending_outcome,
    upsert_readiness,
)
from core.logging import log_event
from core.telegram_client import UNCONFIRMED_ERROR_TYPE
from services.content import release_sent_text
from services.neurocomment import _channel_pause, _state, bans

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult, NewPostEvent

# Solver-clearable write gates: joined the group but a captcha/gate forbids writing.
# Flip readiness off so the pair is no longer selected, and count a write failure —
# a solver click can clear these, so they can retry after re-onboarding.
_GATE_ERRORS = frozenset({"ChatGuestSendForbiddenError", "ChatWriteForbiddenError"})
# Telegram's ACCOUNT-WIDE anti-spam write restriction (what @SpamBot reports as
# limited) — NOT a per-chat moderator action, which arrives as ChatWriteForbiddenError
# (mute) or ChannelPrivateError / UserNotParticipantError (kick), both handled below.
# So it is only a *prompt* to check whether this group actually banned us; the sticky
# ban (#30) and the leave are gated on ``bans.confirm_group_ban_and_leave``. Never a
# write failure either way (no pending-resolve, no channel pause).
_BAN_ERROR = "UserBannedInChannelError"
# The account is no longer in / can no longer reach the discussion group (kicked, group
# went private). Neither a ban nor a solver-clearable gate — the only fix is a fresh join,
# so the pair is parked with onboarding's hard-join-failure sentinel (see the branch).
# BOTH names, because both reach here: ``_actions``' tail funnels every unmapped Telethon
# error into ``failed`` carrying ``error_type=type(exc).__name__``, and the discussion-group
# RPCs ``comment_to=`` issues answer USER_NOT_PARTICIPANT once we are out — the meaning
# ``_read``/``_groups`` already give that class. Naming only ChannelPrivateError left a kick
# in the generic tail, which writes NO readiness: the pair kept ready=1 and never carried
# the sentinel ``_rejoin.review_access_lost`` reads, so nothing ever re-joined it.
_LOST_ACCESS_ERRORS = frozenset({"ChannelPrivateError", "UserNotParticipantError"})
# Rate-limit families that carry (or imply) a cooldown rather than a hard fail.
_COOLDOWN_STATUSES = frozenset(
    {"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"},
)
# Faults nobody's account is to blame for: the gateway's own infrastructure
# (``status="unavailable"`` — pool connect / socket / timeout, see
# ``core.telegram_client._action_results._unavailable_result``) and a Gemini 429 that
# exhausted generation (``_generate._gemini_reason``). Neither gets a cooldown — one proxy
# flap would park the fleet channel by channel.
#
# What happens to the CLAIM then splits on one question the gateway answers and we cannot:
# did the request reach Telegram? When it did not (the pool never connected), the claim is
# ``release_claim``d, because marking it ``failed`` is TERMINAL (``_mark_comment`` refuses
# to re-transition it) on a row ``claim_comment`` already refuses to overwrite — a
# seconds-long outage would burn the post for every account, forever — while leaving it
# ``claimed`` is not free either: quota counts ``claimed`` alongside ``posted`` and the
# sweep's reclaim pass only ages a claim out after ``stale_claim_reclaim_seconds``, so the
# account paid a day-cap slot (a THIRD of its day on that channel at the shipped cap of 3)
# for a comment it never sent — for a quarter of an hour, over a fault of ours.
# When the request DID reach Telegram and only the reply was lost
# (``UNCONFIRMED_ERROR_TYPE``), none of that reasoning survives: the comment may be live
# under the post, so the row must not be handed back. See the branch.
_UNAVAILABLE_STATUS = "unavailable"
_RATE_LIMITED_REASON = "gemini_rate_limited"

# In-flight comments per channel: (text, reserved_at). The posted-comment semantic
# dedup only sees *delivered* rows, so two accounts generating near-duplicates inside
# each other's reply-delay window both pass it. This closes that cross-account gap by
# also comparing against comments reserved-but-not-yet-posted. In-memory, single loop
# (no lock); pruned by the dedup window; only used when the threshold is on.
_INFLIGHT: dict[str, list[tuple[str, datetime]]] = {}


def _inflight_texts(channel: str, now: datetime, window_hours: float) -> list[str]:
    """Live in-flight texts for ``channel``, pruning any past the dedup window."""
    cutoff = now - timedelta(hours=window_hours)
    entries = [(t, ts) for (t, ts) in _INFLIGHT.get(channel, []) if ts > cutoff]
    if entries:
        _INFLIGHT[channel] = entries
    else:
        _INFLIGHT.pop(channel, None)
    return [t for (t, _) in entries]


def _add_inflight(channel: str, text: str, now: datetime) -> None:
    _INFLIGHT.setdefault(channel, []).append((text, now))


def _remove_inflight(channel: str, text: str) -> None:
    entries = _INFLIGHT.get(channel)
    if not entries:
        return
    kept = [(t, ts) for (t, ts) in entries if t != text]
    if kept:
        _INFLIGHT[channel] = kept
    else:
        _INFLIGHT.pop(channel, None)


async def _classify_post(
    event: NewPostEvent,
    account_id: str,
    text: str,
    result: ActionResult,
) -> None:
    if result.status == "ok":
        await _commit_delivered(event, account_id, text, result)
        return

    # Every non-ok path frees the claim's reserved text (and its in-flight entry); every
    # one that is the ACCOUNT's failure also burns the row. A posted comment keeps its
    # in-flight entry until the window expires — it is a genuine recent comment other
    # accounts should still dedup against.
    _remove_inflight(event.channel, text)
    await release_sent_text(text)
    if result.status == _UNAVAILABLE_STATUS:
        # Returns before every write below: no cooldown, no readiness write, no write
        # failure. The pair is fine, the gateway was not (see _UNAVAILABLE_STATUS), and its
        # own event name keeps the outage legible instead of masquerading as a post this
        # account could not make. Only the claim differs — see the helper.
        await _resolve_unavailable_claim(event, result)
        await _log_outcome(event, account_id, result, "neurocomment_post_unavailable")
        return
    await mark_comment_failed(event.channel, event.post_id)

    # Set by the one branch that spends a bounded budget, so the line says which refusal
    # this was out of how many (see the ban branch); absent everywhere else, because a
    # counter next to an outcome nothing is counting would be an invention.
    budget: str | None = None
    if result.status in _COOLDOWN_STATUSES:
        # ponytail: MVP drops the lost post — it is NOT requeued for another
        # account. Post volume is low; a requeue is a follow-up if it bites.
        # slow-mode is per-chat → cool only this channel; flood/peer-flood/premium
        # are account-wide.
        scope = event.channel if result.status == "slow_mode_wait" else None
        await _apply_cooldown(account_id, result.flood_wait_seconds, scope)
        event_name = "neurocomment_post_cooldown"
    elif result.error_type == _BAN_ERROR:
        # Confirm THIS group banned us before parking the pair (see _BAN_ERROR). Only a
        # restricted participant record plus a clean @SpamBot reading marks + leaves.
        # Otherwise the block is account-wide as far as anyone can prove — but a group that
        # refuses an account its own record calls ``can_send`` is not harmless either, so
        # THAT reading is counted (#47): the cooldown alone let the same pair come back to
        # the same channel on its next post and be refused again, ten times over three days
        # for zero comments, because a cooldown expires and nothing else remembered. Two of
        # those a day apart inside 48h and ``register_unconfirmed_ban`` takes the confirmed
        # ban's exit — and logs it there, which is why this branch names the POST outcome
        # instead: the pair used to get two identical ``neurocomment_account_banned`` rows.
        # One probe, both verdicts (see ``bans.probe_group_state``). Below the budget the
        # cooldown still stands, bounded and self-expiring, and stops the pair re-selecting
        # until the account recovers.
        state = await bans.probe_group_state(account_id, event.channel)
        if await bans.confirm_group_ban_and_leave(account_id, event.channel, known_state=state):
            event_name = "neurocomment_account_banned"
        else:
            # The position in that budget, and ONLY when the refusal was actually charged
            # to it: this same line is written for a refusal an account-wide limit explains
            # and for one that arrived inside the pair's own cooldown, and neither spends
            # anything. ``None`` there leaves the line exactly as it was, reading its
            # Telegram status; a counted one trades that status for "1/2".
            budget = await bans.register_unconfirmed_ban(
                account_id, event.channel, known_state=state
            )
            await _apply_cooldown(account_id, None, event.channel)
            event_name = "neurocomment_post_ban_unconfirmed"
    elif result.error_type in _LOST_ACCESS_ERRORS:
        # Ordered after the ban, before the gate: all three match disjoint ``error_type``
        # values so order can't change behaviour — it reads terminal → rejoinable →
        # solver-clearable. Previously this fell to the generic tail, which touches no
        # state, so the pair was re-picked on the channel's very next post and failed
        # forever (live DB: 38 such failures vs 19 sends). (joined=False,
        # captcha_passed=True) is onboarding's existing hard-join-failure sentinel, already
        # rendered as ``join_failed`` by board's ``_not_joined_status`` — no schema/board
        # change needed. ready=False stops selection now, and since the row is neither
        # human_skipped nor banned an onboarding pass re-joins it. That recovery is NOT
        # automatic: ``_ensure_onboarding_running`` has no timer — only operator Start, app
        # boot with ``listener_running=1``, and the campaign link/deactivate/assign/
        # set-status reconciles start a pass. Telethon also raises ChannelPrivateError on a
        # stale cached entity, so a *transient* access loss parks the pair until one of
        # those happens (before #279 it recovered on its own, noisily). Not a solver
        # failure: no pending-resolve, no channel pause.
        # The verdict rides along (#44). Both names in this family mean the same thing —
        # we were in the chat and are not any more — so both are retryable: a chat we
        # posted in once can take us back, which is exactly what the re-join rule is for.
        await upsert_readiness(
            account_id,
            event.channel,
            joined=False,
            captcha_passed=True,
            ready=False,
            access_lost_reason=result.error_type,
        )
        event_name = "neurocomment_post_access_lost"
    elif result.error_type in _GATE_ERRORS:
        # A REAL per-group ban lands HERE, not on _BAN_ERROR: an admin mute/ban raises
        # ChatWriteForbiddenError. Confirm first, fall back to the gate; a confirmed ban is
        # PER-ACCOUNT, so the channel is not paused and — as on _BAN_ERROR — no write failure.
        if await bans.confirm_group_ban_and_leave(account_id, event.channel):
            event_name = "neurocomment_account_banned"
        else:
            # Gate: stop selecting this pair until re-onboarded; the click did not work.
            await upsert_readiness(
                account_id,
                event.channel,
                joined=True,
                captcha_passed=False,
                ready=False,
            )
            await resolve_pending_outcome(account_id, event.channel, "failed")
            # A gate is a property of the CHANNEL, not the pair: counting it only on a
            # resolved pending challenge left a channel that issues none unparked (live DB:
            # one forbade all six accounts, 16 times, re-gated forever). Onboarding honours it.
            await _channel_pause.register_write_failure(event.channel, account_id)
            event_name = "neurocomment_post_gated"
    else:
        # A class fix, not a per-error fix: ``core.telegram_client._actions`` funnels every
        # unmapped Telethon exception into one generic ``status="failed"``, so the named
        # families above can never be a complete enumeration — and a tail that touched NO
        # state re-picked the same pair on the channel's very next post, forever (live DB:
        # 230 failed vs 17 posted). That is the loop #279 closed for ChannelPrivateError
        # alone; naming more errors would only move the hole. So the *default* is now safe:
        # park (account, channel) on the duration-less cooldown fallback. Bounded and
        # self-expiring, so an unknown terminal error costs one window instead of an endless
        # retry, and no readiness write — an unknown error is not evidence of lost access.
        await _apply_cooldown(account_id, None, event.channel)
        event_name = "neurocomment_post_failed"
    await _log_outcome(event, account_id, result, event_name, budget=budget)


async def _commit_delivered(
    event: NewPostEvent,
    account_id: str,
    text: str,
    result: ActionResult,
) -> None:
    """Record a comment Telegram accepted — the commit point of the whole pipeline.

    From here the comment IS delivered, so a failure in any of these DB writes must be
    logged, never flip the row to failed (that would mis-report a live comment and free
    its dedup hash for a duplicate). CancelledError still propagates.

    Ends in exactly one line, chosen by the row the delivery landed on: the clean post, the
    reclaim warning INSTEAD of it when the row went terminal underneath us, or an error when
    there is no row left at all.

    No cooldown clearing here: ``in_cooldown`` lazily evicts expired keys, so the clear
    was redundant in the calm case and destructive under concurrency — a task already past
    the selection gate and sleeping in its reply delay would erase a *fresh* flood cooldown
    another task had just parked the account with.
    """
    try:
        if result.message_id is not None:
            # FIRST, and on its own: ``mark_comment_posted`` refuses to re-transition a
            # terminal row, so on a claim the sweep reclaimed to ``failed`` underneath us it
            # wrote NOTHING — id included — and the comment stayed live under the post while
            # invisible to the deletion sweep, which can only see rows carrying an id. The
            # id is a fact, so it lands whatever the status ended up saying.
            await record_comment_msg_id(event.channel, event.post_id, result.message_id)
        record = await mark_comment_posted(
            event.channel,
            event.post_id,
            comment_text=text,
            comment_msg_id=result.message_id,
        )
        # First comment confirms a solver click worked (no-op if no pending row).
        await resolve_pending_outcome(account_id, event.channel, "solved")
        # A delivered comment proves the channel is writable, so it clears both the
        # failure window and the persisted round counter (#147).
        await _channel_pause.clear_write_failures(event.channel)
        # And this PAIR's own unconfirmed-refusal budget (#47), which the channel-wide
        # reset above cannot speak for: the count that bans a pair is per (account,
        # channel), so another account's success must not spend or refund it.
        await clear_unconfirmed_bans(account_id, event.channel)
        # Exactly ONE line per delivery, describing the row this delivery actually landed
        # on. The clean line used to fire in addition to the warning, so the feed announced
        # "Comment posted" over a row reading ``failed`` — the very contradiction the
        # warning exists to report.
        if record is None:
            # No row at all: the id write above went nowhere either, so a live comment is
            # under the post with nothing recording it — invisible to the deletion sweep and
            # to every counter. Only ``release_claim`` can delete a row, and only this worker
            # calls it, so this should be unreachable; ERROR because nothing else can see it.
            await log_event(
                "ERROR",
                "neurocomment_posted_row_missing",
                account_id=account_id,
                extra={"channel": event.channel, "post_id": event.post_id},
            )
        elif record.status != "posted":
            # The row was already terminal, and this is the only honest place to say so:
            # the campaign's counters quietly under-count a comment that did land.
            await log_event(
                "WARNING",
                "neurocomment_posted_after_reclaim",
                account_id=account_id,
                extra={
                    "channel": event.channel,
                    "post_id": event.post_id,
                    "status": record.status,
                },
            )
        else:
            await log_event(
                "INFO",
                "neurocomment_posted",
                account_id=account_id,
                extra={"channel": event.channel, "post_id": event.post_id},
            )
    except Exception:  # noqa: BLE001 - a delivered comment must not be flipped to failed
        await log_event(
            "ERROR",
            "neurocomment_post_commit_failed",
            account_id=account_id,
            extra={"channel": event.channel, "post_id": event.post_id},
        )


async def _resolve_unavailable_claim(event: NewPostEvent, result: ActionResult) -> None:
    """Settle the claim of a gateway outage, on what that outage can actually prove.

    Nothing was sent (the pool never connected) → release. The DELETE is what makes "not
    charged" true of the quota too, not just of the status, and it hands the post back so
    another account — or this one, later — can still comment on it.

    The send went out and only the answer was lost (``UNCONFIRMED_ERROR_TYPE``) → the
    comment may be LIVE under the post, and releasing then re-opened the very window
    ``claim_comment`` exists to close: Telethon closes an updates gap by re-delivering the
    post it missed, that re-delivery wins a fresh claim, and we comment a SECOND time under
    a post we may already have commented on. So the row is burnt instead. ``failed`` frees
    the quota slot just as immediately (``_quota`` counts only ``claimed`` and ``posted``)
    while staying terminal, and it is the same verdict ``_sweep._reclaim_stale_claims``
    already writes for the same ambiguity — the honest record of an attempt we cannot
    confirm delivered. Under-counting a comment that did land is the residue of not
    knowing; counting it instead over-counts every time it did not, and holds the slot too.
    """
    if result.error_type == UNCONFIRMED_ERROR_TYPE:
        await mark_comment_failed(event.channel, event.post_id)
    else:
        await release_claim(event.channel, event.post_id)


async def _log_outcome(
    event: NewPostEvent,
    account_id: str,
    result: ActionResult,
    event_name: str,
    *,
    budget: str | None = None,
) -> None:
    """Report one non-delivered post outcome (shared by the ladder and its early exit).

    ``error_type`` is the Telegram exception class behind ``status``: the feed reported a
    failed post without ever saying why, and the reason was already in hand at the call
    site. Absent rather than null when the gateway set none (the flood family never does).

    ``budget`` is the outcome's position in a bounded rule ("1/2"), written as ``reason``
    — the field the SPA already renders next to the event label, so a ratio needs neither
    a translation nor a new event code. It DISPLACES the Telegram status in that slot
    (``eventReason`` reads ``status`` only when there is no ``reason``), which is the
    trade the one caller that sets it makes on purpose: on that line the status is the
    invariant ``failed`` and ``error_type`` still names the refusal, while how far the
    rule has got is the part the operator cannot deduce.
    """
    extra: dict[str, object] = {
        "channel": event.channel,
        "post_id": event.post_id,
        "status": result.status,
    }
    if budget is not None:
        extra["reason"] = budget
    if result.error_type:
        extra["error_type"] = result.error_type
    await log_event("WARNING", event_name, account_id=account_id, extra=extra)


async def _apply_cooldown(
    account_id: str, flood_wait_seconds: int | None, channel: str | None
) -> None:
    """Park ``(account, channel)``: flood duration, else the peer-flood config default."""
    seconds = flood_wait_seconds
    if seconds is None:
        # peer_flood (and any wait without a duration) → config cooldown.
        seconds = int(settings.neurocomment.peer_flood_cooldown_seconds)
    await _state.set_cooldown(account_id, datetime.now(UTC) + timedelta(seconds=seconds), channel)
