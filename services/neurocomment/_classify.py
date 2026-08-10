"""Join-outcome classification + solver recording — split out of ``onboarding``.

The join ``ActionResult`` → ``OnboardingState`` mapping (plus the proactive
challenge-solver recording it delegates to) lives here to keep
:mod:`services.neurocomment.onboarding` under the aislop file-size cap. The
error-family constants move with it since they are only read here. Everything is
re-exported back into ``onboarding`` so ``onboarding._classify_join`` and callers
still resolve unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    clear_join_request,
    clear_rejoin_attempts,
    fetch_readiness,
    stamp_join_request,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, OnboardingState
from schemas.telegram_actions_rights import CheckWriteRights, WriteRightsResult
from services.neurocomment import _channel_pause, _comments_off, _seams, bans, challenge

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult

# Writes Telegram-blocked at join → chat_restricted (Ф2 #120); solver can't clear it.
_GATE_ERRORS = frozenset({"ChatGuestSendForbiddenError", "ChatWriteForbiddenError"})
# Telegram's ACCOUNT-WIDE anti-spam write restriction (what @SpamBot reports as limited),
# not a per-chat moderator action — those arrive as the _GATE_ERRORS above (mute) or as
# ChannelPrivateError (kick). So it only prompts the per-group confirmation ladder; the
# sticky ban (#30) is gated on it, same as a ban hit while commenting.
_BAN_ERROR = "UserBannedInChannelError"
# Rate-limit families: never terminal, retry later, must return promptly.
_RETRY_STATUSES = frozenset({"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"})


async def _classify_join(
    account_id: str,
    channel: str,
    result: ActionResult,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Map a join ``ActionResult`` to a state + persisted readiness row."""
    if result.status in {"ok", "already_participant"}:
        # Joined (or already a member) → run the proactive challenge solver before
        # declaring the pair comment-able (Ф2 #145), unless the solver is disabled
        # for this campaign. ``rejoined`` tells the two apart, and only for the re-join
        # counter — see ``_solve_and_record``.
        return await _solve_and_record(
            account_id,
            channel,
            group_id,
            solver_enabled=solver_enabled,
            rejoined=result.status == "ok",
        )
    if result.status in _RETRY_STATUSES:
        # Non-terminal: do not write ready; surface the wait so the account is
        # retried later instead of getting stuck. Return promptly (no sleep).
        await log_event(
            "INFO",
            "neurocomment_onboard_retry_later",
            account_id=account_id,
            extra={"channel": channel, "status": result.status},
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="joining",
            reason=result.error_type or f"{result.status}:{result.flood_wait_seconds}",
        )
    if result.error_type == "InviteRequestSentError":
        await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
        # Stamp the request AFTER the upsert (which cannot carry these columns without
        # a re-onboard resetting them). The stamp is what stops the next pass re-sending
        # the same request: it is the only thing distinguishing this row from the
        # challenge-backoff row, which is identical field for field.
        await stamp_join_request(account_id, channel)
        # The state itself was invisible in the log: only the gateway's join line was
        # written, so an operator could not tell "waiting for admin approval" from a
        # broken join, and the channel just silently produced no comments.
        #
        # "1/2" rides along as the reason, the same ratio the pending line carries, so
        # the operator reads one running count instead of a first request that says
        # nothing and a follow-up that suddenly knows its budget. The count comes off a
        # re-read because ``stamp_join_request`` returns nothing and the row is the only
        # thing that knows how many requests this pair has actually sent; a point read is
        # affordable here where it would not be on the post path — this branch runs at
        # most ``join_request_max_attempts`` times in a pair's life.
        stamped = await fetch_readiness(account_id, channel)
        attempts = stamped.join_request_attempts if stamped is not None else 1
        await log_event(
            "INFO",
            "neurocomment_onboard_join_by_request",
            account_id=account_id,
            extra={
                "channel": channel,
                "attempts": attempts,
                "reason": f"{attempts}/{settings.neurocomment.join_request_max_attempts}",
            },
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="join_by_request",
        )
    if result.error_type == _BAN_ERROR:
        # Readiness first either way: we are in (or reachable from) the group but cannot
        # write, so the pair must stop being selected. The sticky ban (#30) that also
        # stops a re-onboard re-joining is gated on THIS group having actually banned us
        # — otherwise the block is account-wide and the group is innocent, which is
        # exactly what chat_restricted (a Telegram-level write block) already says.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        confirmed = await bans.confirm_group_ban_and_leave(account_id, channel)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="banned" if confirmed else "chat_restricted",
        )
    if result.error_type in _GATE_ERRORS:
        # Telegram-level write block (mute / restrict) → chat_restricted (Ф2 #120).
        # Unsolvable by the challenge solver, so it is never invoked here; joined stays
        # True (we are a member) but ready is False. Written FIRST and unconditionally,
        # exactly as before: whatever the probe below says, the pair must stop being
        # selected, and that has to survive a probe that dies.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return await _classify_write_block(account_id, channel)
    # Hard failure (invalid invite / banned / private): never joined and never will
    # without operator action. Persist a signal distinct from the approval-gate row
    # (which is also joined=False) so the board renders join_failed, not "awaiting
    # approval": captcha_passed=True on an unjoined row is the sentinel (no other path
    # produces that combination). ready stays False so the pair is never selected.
    # The verdict rides along (#44) so the re-join rule can tell a chat that might let us
    # in later from an address that will never resolve. The error CLASS only: the message
    # is free-form text no rule can key on, and a wrong reading here costs the channel its
    # whole 48h re-join budget.
    await upsert_readiness(
        account_id,
        channel,
        joined=False,
        captcha_passed=True,
        ready=False,
        access_lost_reason=result.error_type,
    )
    return AccountChannelOnboarding(
        account_id=account_id,
        channel=channel,
        state="failed",
        reason=result.error_type or result.error_message,
    )


async def _write_block_scope(account_id: str, channel: str) -> WriteRightsResult:
    """Ask Telegram WHOSE mute this is — the one extra RPC a refused write buys.

    Only ever on an actual refusal, never speculatively and never on a schedule: the whole
    point of this project is not getting accounts frozen, so the budget is one read per
    refusal.

    Never raises. ``execute_read`` throws (``TelegramReadError`` on flood/RPC,
    account-not-found, or a wrong type) and an unknown must never become a verdict — that
    is the mistake the caller exists to prevent. ``TelegramReadError`` already collapses
    its cause to ``RPC: <ClassName>``, so its ``reason`` is carried through rather than
    spelled a second time here.
    """
    try:
        rights = await _seams.execute_read(account_id, CheckWriteRights(channel=channel))
    except Exception as exc:  # noqa: BLE001 - an unreadable answer is not a verdict.
        return WriteRightsResult(scope="unknown", reason=getattr(exc, "reason", type(exc).__name__))
    if not isinstance(rights, WriteRightsResult):  # pragma: no cover - typed gateway
        return WriteRightsResult(scope="unknown", reason="unexpected_result")
    return rights


async def _classify_write_block(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Turn "we cannot write here" into WHOSE doing it is, and hand it to the owning rule.

    ``ChatWriteForbiddenError`` collapses two situations that need opposite responses — a
    chat closed to everyone, where the CHANNEL is what should leave service, and a mute on
    this one account, where nothing should be spent and nobody should leave. Before this
    read they were indistinguishable, and a muted pair was retired as a lost captcha fight
    it was never in. Each of the three answers has an owner:

    * ``everyone`` → ``_comments_off``, which already IS the rule for "this channel's
      comments are off" and already unlinks through the service so the listener
      reconciles. ``report_and_drop``, not ``recheck``: recheck re-asks
      ``full_chat.linked_chat_id``, which a read-only group still answers, so it would
      find nothing wrong. That module insists its verdict be authoritative rather than
      guessed at, and this one is — the chat-wide ``default_banned_rights`` read off the
      group entity, not a resolve that merely failed.
    * ``self_only`` → nothing terminal, nothing spent, nobody leaves. The readiness row
      the caller already wrote stops the pair being selected, and the mute's own expiry is
      the window we sit out (``_channel_pause.hold_muted_pair``, which bounds it).
    * anything else → exactly today's behaviour, ``chat_restricted`` and no action. The
      reason rides along so a probe that never answers is visible instead of silent.
    """
    rights = await _write_block_scope(account_id, channel)
    if rights.scope == "everyone":
        await _comments_off.report_and_drop(channel, account_id)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="comments_off",
            reason="write_blocked_for_everyone",
        )
    if rights.scope == "self_only":
        held_until = await _channel_pause.hold_muted_pair(account_id, channel, rights.muted_until)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="chat_restricted",
            reason=f"muted_until:{held_until}",
        )
    return AccountChannelOnboarding(
        account_id=account_id,
        channel=channel,
        state="chat_restricted",
        reason=rights.reason,
    )


async def _solve_and_record(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
    rejoined: bool = True,
) -> AccountChannelOnboarding:
    """Run the challenge solver on a freshly-joined group; persist the readiness.

    With the solver disabled (opt-in, #148) an ok join is assumed comment-able →
    ``ready``. Otherwise ``give_up`` / ``failed`` (a detected/unsolved challenge)
    leaves the pair not-ready — the audit row drives the board's ``bot_challenge``;
    ``no_challenge`` / ``solved`` means comment-able → ``ready``.
    """

    def _result(state: OnboardingState) -> AccountChannelOnboarding:
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state=state)

    # We are a member, so any earlier approval request landed — drop its stamp here
    # rather than in the ready branch alone: a joined-but-challenged pair is approved
    # too, and leaving the counter at max would make the sweep drop a live channel.
    await clear_join_request(account_id, channel)
    # Same idea for the re-join counter (#43): we are in the group, so whatever kicked us
    # out is over and the next access loss must start from attempt one. Cleared here
    # rather than in the ready branch alone — a joined-but-challenged pair is back in too,
    # and leaving it at its cap would make the sweep drop a channel we just re-entered.
    #
    # Only a join that ACTUALLY happened resets it. ``already_participant`` means Telegram
    # never let us out in the first place, i.e. the parking that spent the attempt was
    # wrong — a stale group entity in the session cache, or a channel-level refusal read as
    # an account-level one. Resetting on that closed a loop with no bound: the sweep parks
    # the pair, the re-join review spends an attempt and pokes onboarding, the join answers
    # ``already_participant``, the counter goes back to zero, and five minutes later the
    # same tick does it again — up to 288 join RPCs a day for one pair, invisible to the
    # rolling-24h join cap because ``record_join`` only counts a real join. Keeping the
    # count makes the budget bound it: two such rounds and the review leaves the pair alone.
    if rejoined:
        await clear_rejoin_attempts(account_id, channel)
    if solver_enabled:
        outcome = await challenge.solve_if_present(account_id, channel, group_id)
        if outcome == "rate_limited":
            # LLM gateway 429'd: transient, not a solver failure — retry-later, no
            # readiness written, un-penalized (no bot_challenge, no #147 back-off).
            return _result("joining").model_copy(update={"reason": "llm_rate_limited"})
        if outcome in ("give_up", "failed"):
            # Detected but unsolved (or click errored) → not comment-able; the solver's
            # audit row is what the board reads to render bot_challenge.
            await upsert_readiness(
                account_id, channel, joined=True, captcha_passed=False, ready=False
            )
            return _result("bot_challenge")
    # Solver disabled, or no_challenge/solved (click dispatched, audit pending) →
    # optimistically comment-able; the engine confirms a solved click on first comment.
    await upsert_readiness(account_id, channel, joined=True, captcha_passed=True, ready=True)
    return _result("ready")
