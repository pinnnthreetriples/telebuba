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
    mark_comment_failed,
    mark_comment_posted,
    release_claim,
    resolve_pending_outcome,
    upsert_readiness,
)
from core.logging import log_event
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
# flap would park the fleet channel by channel — and neither marks the claim ``failed``:
# that status is TERMINAL (``_mark_comment`` refuses to re-transition it) on a row
# ``claim_comment`` already refuses to overwrite, so a seconds-long outage would burn the
# post for every account, forever. The claim is ``release_claim``d instead: leaving it
# ``claimed`` is not free either, because quota counts ``claimed`` alongside ``posted`` and
# the sweep's reclaim pass only ages a claim out after ``stale_claim_reclaim_seconds``, so
# the account paid a day-cap slot (a THIRD of its day on that channel at the shipped cap of
# 3) for a comment it never sent — for a quarter of an hour, over a fault of ours.
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
        # Telegram accepted the comment — this is the commit point. From here the
        # comment IS delivered, so a failure in any of the follow-up DB writes must be
        # logged, never flip the row to failed (that would mis-report a live comment
        # and free its dedup hash for a duplicate). CancelledError still propagates.
        # No cooldown clearing here: ``in_cooldown`` lazily evicts expired keys, so the
        # clear was redundant in the calm case and destructive under concurrency — a task
        # already past the selection gate and sleeping in its reply delay would erase a
        # *fresh* flood cooldown another task had just parked the account with.
        try:
            await mark_comment_posted(
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
        return

    # Every non-ok path frees the claim's reserved text (and its in-flight entry); every
    # one that is the ACCOUNT's failure also burns the row. A posted comment keeps its
    # in-flight entry until the window expires — it is a genuine recent comment other
    # accounts should still dedup against.
    _remove_inflight(event.channel, text)
    await release_sent_text(text)
    if result.status == _UNAVAILABLE_STATUS:
        # Returns before every write below: no burnt claim, no cooldown, no readiness
        # write, no write failure. The pair is fine, the gateway was not (see
        # _UNAVAILABLE_STATUS), and its own event name keeps the outage legible instead of
        # masquerading as a post this account could not make. Releasing the claim is what
        # makes "not charged" true of the quota too, not just of the status.
        await release_claim(event.channel, event.post_id)
        await _log_outcome(event, account_id, result, "neurocomment_post_unavailable")
        return
    await mark_comment_failed(event.channel, event.post_id)

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
        # restricted participant record plus a clean @SpamBot reading marks + leaves;
        # otherwise the block is account-wide and the group is innocent, so the pair only
        # gets the duration-less cooldown — bounded and self-expiring, and enough to stop
        # it re-selecting and looping on the same error until the account recovers.
        if await bans.confirm_group_ban_and_leave(account_id, event.channel):
            event_name = "neurocomment_account_banned"
        else:
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
    await _log_outcome(event, account_id, result, event_name)


async def _log_outcome(
    event: NewPostEvent,
    account_id: str,
    result: ActionResult,
    event_name: str,
) -> None:
    """Report one non-delivered post outcome (shared by the ladder and its early exit).

    ``error_type`` is the Telegram exception class behind ``status``: the feed reported a
    failed post without ever saying why, and the reason was already in hand at the call
    site. Absent rather than null when the gateway set none (the flood family never does).
    """
    extra: dict[str, object] = {
        "channel": event.channel,
        "post_id": event.post_id,
        "status": result.status,
    }
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
