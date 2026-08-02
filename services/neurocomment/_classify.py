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

from core.db import (
    clear_join_request,
    clear_rejoin_attempts,
    stamp_join_request,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, OnboardingState
from services.neurocomment import bans, challenge

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
        # for this campaign.
        return await _solve_and_record(account_id, channel, group_id, solver_enabled=solver_enabled)
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
        await log_event(
            "INFO",
            "neurocomment_onboard_join_by_request",
            account_id=account_id,
            extra={"channel": channel},
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
        # True (we are a member) but ready is False.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="chat_restricted",
        )
    # Hard failure (invalid invite / banned / private): never joined and never will
    # without operator action. Persist a signal distinct from the approval-gate row
    # (which is also joined=False) so the board renders join_failed, not "awaiting
    # approval": captcha_passed=True on an unjoined row is the sentinel (no other path
    # produces that combination). ready stays False so the pair is never selected.
    await upsert_readiness(account_id, channel, joined=False, captcha_passed=True, ready=False)
    return AccountChannelOnboarding(
        account_id=account_id,
        channel=channel,
        state="failed",
        reason=result.error_type or result.error_message,
    )


async def _solve_and_record(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
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
