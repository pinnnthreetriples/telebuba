"""Per-channel + per-pair onboarding helpers extracted from ``onboarding``.

Split out so :mod:`services.neurocomment.onboarding` stays under the aislop
file-size cap. The public entrypoint (``onboard_campaign``) still lives there
and only threads an :class:`OnboardContext` through these helpers.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding
from schemas.neurocomment_progress import OnboardingProgressEvent

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclasses.dataclass(frozen=True, slots=True)
class OnboardContext:
    """Per-campaign onboarding state threaded through the channel + pair helpers.

    Packs the otherwise-many parameters (accounts, already_ready set, outcomes
    accumulator, solver flag, progress callbacks) into one value so the helpers
    stay under the PLR0913 argument-count limit.

    ``pins`` maps each account to its channel subset (empty = all channels). An
    account with a subset is onboarded only against those channels — ``accounts_for``
    filters the per-channel account list.
    """

    accounts: list[str]
    already_ready: set[tuple[str, str]]
    outcomes: list[AccountChannelOnboarding]
    solver_enabled: bool
    on_progress: Callable[[OnboardingProgressEvent], None] | None
    report: Callable[[OnboardingProgressEvent], None]
    pins: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    def accounts_for(self, channel: str) -> list[str]:
        """Accounts eligible for ``channel``: empty subset, or subset holding it."""
        return [
            account_id
            for account_id in self.accounts
            if not self.pins.get(account_id) or channel in self.pins[account_id]
        ]


async def onboard_channel(channel: str, ctx: OnboardContext, *, joined_once: bool) -> bool:
    """Onboard every eligible account on one channel; return the updated flag.

    Only accounts unpinned or pinned to this channel are onboarded (``accounts_for``).
    Compute the "remaining" account list = eligible accounts NOT already ready for
    THIS channel. If every pair is ready, skip the Telegram resolve entirely (anti-ban
    + a fully-prepared channel costs zero reads). A transient resolve failure here
    would otherwise clobber the already-ready pairs with "failed" outcomes (Bug 3).
    """
    # Lazy import keeps this module free of ``onboarding`` at import time (the
    # parent module consumes this one's public API, so a top-level import would
    # cycle).
    from services.neurocomment import onboarding  # noqa: PLC0415

    eligible = ctx.accounts_for(channel)
    remaining = [acc for acc in eligible if (acc, channel) not in ctx.already_ready]
    if not remaining:
        ctx.report(OnboardingProgressEvent(code="channel_all_ready", channel=channel))
        ctx.outcomes.extend(
            AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
            for account_id in eligible
        )
        return joined_once
    ctx.report(OnboardingProgressEvent(code="channel_resolving", channel=channel))
    group_id = await onboarding._resolve_group_for_join(  # noqa: SLF001 - peer module
        remaining, channel, ctx.outcomes, report=ctx.on_progress
    )
    if group_id is None:
        ctx.outcomes.extend(
            AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
            for account_id in eligible
            if (account_id, channel) in ctx.already_ready
        )
        return joined_once
    for account_id in eligible:
        joined_once = await _onboard_pair(
            account_id, channel, group_id, ctx, joined_once=joined_once
        )
    return joined_once


async def _onboard_pair(
    account_id: str,
    channel: str,
    group_id: int,
    ctx: OnboardContext,
    *,
    joined_once: bool,
) -> bool:
    """Onboard one (account, channel) pair; return the updated ``joined_once`` flag."""
    from services.neurocomment import onboarding  # noqa: PLC0415

    if (account_id, channel) in ctx.already_ready:
        ctx.report(
            OnboardingProgressEvent(
                code="pair_already_ready", account_id=account_id, channel=channel
            )
        )
        ctx.outcomes.append(
            AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
        )
        return joined_once
    # Rolling-24h join cap (anti-freeze): an account at its cap has this join skipped —
    # no RPC, no jitter sleep, joined_once unchanged — and retries once the window rolls.
    # Reuses the non-terminal "joining" outcome so the pair is retried, not stuck.
    if await onboarding._at_join_cap(account_id):  # noqa: SLF001 - peer module
        await log_event(
            "WARNING",
            "neurocomment_join_daily_cap",
            account_id=account_id,
            extra={"channel": channel},
        )
        outcome = AccountChannelOnboarding(
            account_id=account_id, channel=channel, state="joining", reason="daily_join_cap"
        )
        ctx.report(
            OnboardingProgressEvent(
                code="pair_result",
                account_id=account_id,
                channel=channel,
                state=outcome.state,
                reason=outcome.reason,
            )
        )
        ctx.outcomes.append(outcome)
        return joined_once
    if joined_once:
        jitter = onboarding._join_jitter_seconds()  # noqa: SLF001 - peer module
        ctx.report(
            OnboardingProgressEvent(code="pair_join_delay", channel=channel, delay_seconds=jitter)
        )
        await asyncio.sleep(jitter)
    ctx.report(OnboardingProgressEvent(code="pair_joining", account_id=account_id, channel=channel))
    outcome = await onboarding._join_pair_safely(  # noqa: SLF001 - peer module
        account_id, channel, group_id, solver_enabled=ctx.solver_enabled
    )
    ctx.report(
        OnboardingProgressEvent(
            code="pair_result",
            account_id=outcome.account_id,
            channel=channel,
            state=outcome.state,
            reason=outcome.reason,
        )
    )
    ctx.outcomes.append(outcome)
    return True
