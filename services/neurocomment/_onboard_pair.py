"""Single-pair onboarding helpers extracted from ``onboarding``.

Split out so :mod:`services.neurocomment.onboarding` stays under the aislop
file-size cap. These helpers resolve one channel's linked discussion group,
join it for one account, classify the join result, and run the challenge
solver — persisting the pair's readiness. The public entrypoint
``onboard_account_channel`` and the campaign loop's ``_join_pair_safely`` /
``_resolve_group_for_join`` both build on this chain; ``onboarding`` re-exports
the names it (and the tests) reach for.

All Telegram and randomness access goes through ``_seams`` so tests patch one
place.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    fetch_readiness,
    mark_pair_banned,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, OnboardingState
from schemas.telegram_actions import (
    ActionResult,
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services.neurocomment import _seams, _state, challenge

# Writes Telegram-blocked at join → chat_restricted (Ф2 #120); solver can't clear it.
_GATE_ERRORS = frozenset({"ChatGuestSendForbiddenError", "ChatWriteForbiddenError"})
# A hard ban at join → sticky ban (#30), same as a ban hit while commenting (never retried).
_BAN_ERROR = "UserBannedInChannelError"
# Rate-limit families: never terminal, retry later, must return promptly.
_RETRY_STATUSES = frozenset({"flood_wait", "slow_mode_wait", "premium_wait", "peer_flood"})


def _effective_solver_enabled(campaign_override: bool | None) -> bool:  # noqa: FBT001 - tri-state value
    """Per-campaign solver override beats the global flag; both default off (#148)."""
    if campaign_override is not None:
        return campaign_override
    return settings.neurocomment.challenge_solver_enabled


async def onboard_account_channel(account_id: str, channel: str) -> AccountChannelOnboarding:
    """Prepare one account to comment on one channel; persist its readiness."""
    linked = await _safe_resolve(account_id, channel)
    if linked is None:
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="failed",
            reason="resolve_failed",
        )
    if not linked.comments_enabled or linked.linked_chat_id is None:
        # comments_off is a channel property, not a per-account state, so we
        # record no readiness row — the campaign loop also short-circuits it.
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="comments_off",
        )
    campaign = await fetch_active_campaign_for_channel(channel)
    solver_enabled = _effective_solver_enabled(campaign.solver_enabled if campaign else None)
    return await _join_and_classify(
        account_id, channel, linked.linked_chat_id, solver_enabled=solver_enabled
    )


async def _join_and_classify(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Join the (already-resolved, comment-enabled) group and persist readiness.

    A channel in challenge back-off (#147, K solver failures) is left alone — no
    join, no solver — until its cooldown expires; the board renders it
    ``bot_challenge_backoff`` from the in-memory back-off state.

    An operator-skipped pair (#148) or an auto-banned pair (#30) is left alone:
    re-joining would run the solver and flip readiness back to ready, undoing the
    skip / reviving the ban. Cleared via ``retry_pair`` (skip) or a can_send probe (ban).
    """
    existing = await fetch_readiness(account_id, channel)
    if existing is not None and (existing.human_skipped or existing.banned):
        state = "human_skipped" if existing.human_skipped else "banned"
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state=state)
    if _state.is_channel_in_challenge_backoff(channel, datetime.now(UTC)):
        await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="bot_challenge_backoff",
        )
    result = await _seams.execute(account_id, JoinDiscussionGroup(channel=channel))
    return await _classify_join(
        account_id, channel, result, group_id, solver_enabled=solver_enabled
    )


async def _resolve_linked_group(account_id: str, channel: str) -> LinkedDiscussionGroupResult:
    """Read the channel's linked discussion group and cache the resolution."""
    linked = await _seams.execute_read(account_id, GetLinkedDiscussionGroup(channel=channel))
    if not isinstance(linked, LinkedDiscussionGroupResult):  # pragma: no cover - typed gateway
        msg = f"Unexpected read result for {channel!r}: {type(linked).__name__}"
        raise TypeError(msg)
    await upsert_linked_group(
        channel,
        linked.linked_chat_id,
        comments_enabled=linked.comments_enabled,
    )
    return linked


async def _safe_resolve(account_id: str, channel: str) -> LinkedDiscussionGroupResult | None:
    """Resolve+cache a channel's linked group; on any gateway failure, log and return None.

    ``execute_read`` *raises* (``TelegramReadError`` on flood/RPC, account-not-found,
    or a wrong type) rather than returning a typed error, so one channel's resolve
    must never abort the campaign loop — mirrors ``_join_pair_safely``.
    """
    try:
        return await _resolve_linked_group(account_id, channel)
    except Exception as exc:  # noqa: BLE001 - one channel must never abort the campaign
        await log_event(
            "ERROR",
            "neurocomment_onboard_resolve_failed",
            account_id=account_id,
            extra={"channel": channel, "error_type": type(exc).__name__},
        )
        return None


async def _classify_join(
    account_id: str,
    channel: str,
    result: ActionResult,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Map a join ``ActionResult`` to a state + persisted readiness row."""
    if result.status == "ok":
        # Joined → run the proactive challenge solver before declaring the pair
        # comment-able (Ф2 #145), unless the solver is disabled for this campaign.
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
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="join_by_request",
        )
    if result.error_type == _BAN_ERROR:
        # Hard ban at join → sticky ban (#30) so a re-onboard stops re-joining the group.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        await mark_pair_banned(account_id, channel)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="banned",
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
