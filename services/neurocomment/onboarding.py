"""Campaign pre-onboarding — prepare accounts so a fresh post is commentable.

For each (account, channel) we resolve the channel's linked discussion group,
join it, and persist readiness, so the hot path (a brand-new post) never pays
the join cost. Captcha is *detect-and-skip only* here — solving entry captchas
is deferred to spike #120; the comment engine (#118) lazily marks captcha when
a comment is actually forbidden.

Outcome states (``OnboardingState``):
- ``ready``          — joined and (MVP) assumed comment-able.
- ``comments_off``   — channel has comments disabled; nothing to join.
- ``join_by_request``— group is approval-gated; request sent, not a member.
- ``chat_restricted``— joined but writes are Telegram-blocked (mute/restrict/ban).
- ``joining``        — rate-limited; retry later, account not stuck.
- ``failed``         — any other error.

All Telegram and randomness access goes through ``_seams`` so tests patch one
place; the inter-join sleep uses ``asyncio.sleep`` (patched in tests).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from core.config import settings
from core.db import (
    fetch_active_campaign_for_channel,
    fetch_campaign,
    list_campaign_accounts,
    list_campaign_channels,
    upsert_linked_group,
    upsert_readiness,
)
from core.logging import log_event
from schemas.neurocomment import AccountChannelOnboarding, CampaignOnboardingResult
from schemas.telegram_actions import (
    ActionResult,
    GetLinkedDiscussionGroup,
    JoinDiscussionGroup,
    LinkedDiscussionGroupResult,
)
from services.neurocomment import _seams, _state, challenge

# Join failed because writes are Telegram-blocked → chat_restricted (Ф2 #120):
# unsolvable by the challenge solver. The set is small and intentional.
_GATE_ERRORS = frozenset(
    {"ChatGuestSendForbiddenError", "ChatWriteForbiddenError", "UserBannedInChannelError"},
)
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
    """
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
    if result.error_type in _GATE_ERRORS:
        # Telegram-level write block (mute / restrict / ban) → chat_restricted (Ф2
        # #120). Unsolvable by the challenge solver, so it is never invoked here;
        # joined stays True (we are a member) but ready is False.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="chat_restricted",
        )
    await upsert_readiness(account_id, channel, joined=False, captcha_passed=False, ready=False)
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
    if not solver_enabled:
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=True, ready=True)
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
    outcome = await challenge.solve_if_present(account_id, channel, group_id)
    if outcome in ("give_up", "failed"):
        # Detected but unsolved (or the click errored) → not comment-able; the
        # solver's audit row is what the board reads to render bot_challenge.
        await upsert_readiness(account_id, channel, joined=True, captcha_passed=False, ready=False)
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="bot_challenge",
        )
    # no_challenge, or solved (click dispatched, audit pending) → optimistically
    # comment-able; the engine confirms a solved click on the first comment.
    await upsert_readiness(account_id, channel, joined=True, captcha_passed=True, ready=True)
    return AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")


async def onboard_campaign(
    campaign_id: str,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> CampaignOnboardingResult:
    """Onboard every (account, channel) pair of a campaign with paced joins.

    Resolves each channel's linked group once: a comments-off channel records
    one ``comments_off`` outcome per account and is never joined. Between real
    joins we sleep a jittered ``rng.uniform(join_delay_min, join_delay_max)``
    for anti-ban pacing. A single failing pair is logged and skipped — the loop
    never raises.
    """
    campaign = await fetch_campaign(campaign_id)
    if campaign is None:
        return CampaignOnboardingResult(campaign_id=campaign_id)

    channels = (await list_campaign_channels(campaign_id)).links
    accounts = [link.account_id for link in (await list_campaign_accounts(campaign_id)).links]
    solver_enabled = _effective_solver_enabled(campaign.solver_enabled)

    def report(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    report(f"Запуск онбординга для {len(accounts)} аккаунтов и {len(channels)} каналов...")

    # Establish each serving account's spam verdict up front. Selection reads this
    # from cache and never re-probes @SpamBot per post (anti-ban), so a verdict must
    # exist before the account goes on the line.
    await _probe_account_spam(accounts, report=on_progress)

    outcomes: list[AccountChannelOnboarding] = []
    joined_once = False
    for channel_link in channels:
        channel = channel_link.channel
        report(f"Разрешение группы обсуждения для {channel}...")
        # Resolve the linked group ONCE per channel (anti-ban + fewer reads).
        group_id = await _resolve_group_for_join(accounts, channel, outcomes, report=on_progress)
        if group_id is None:
            continue
        for account_id in accounts:
            if joined_once:
                jitter = _join_jitter_seconds()
                report(f"Пауза {jitter:.1f} сек для обхода спам-фильтров...")
                await asyncio.sleep(jitter)
            report(f"Аккаунт {account_id}: вступление в группу для {channel}...")
            outcome = await _join_pair_safely(
                account_id, channel, group_id, solver_enabled=solver_enabled
            )
            status_ru = {
                "ready": "готов",
                "comments_off": "комментарии отключены",
                "join_by_request": "отправлена заявка",
                "chat_restricted": "ограничен в записи",
                "joining": "ожидание лимитов",
                "bot_challenge_backoff": "пауза капчи",
                "bot_challenge": "требуется капча",
                "failed": "ошибка",
            }.get(outcome.state, outcome.state)
            reason_str = f" ({outcome.reason})" if outcome.reason else ""
            report(f"Результат для {account_id} на {channel}: {status_ru}{reason_str}")
            outcomes.append(outcome)
            joined_once = True
    ready_count = sum(1 for o in outcomes if o.state == "ready")
    report(f"Онбординг завершен. Готово пар: {ready_count} из {len(outcomes)}.")
    return CampaignOnboardingResult(campaign_id=campaign_id, outcomes=outcomes)


async def _resolve_group_for_join(
    accounts: list[str],
    channel: str,
    outcomes: list[AccountChannelOnboarding],
    report: Callable[[str], None] | None = None,
) -> int | None:
    """Resolve+cache the channel's group once; record per-account skips, return its id.

    Uses the first account's session for the read (any member-less read works). A
    resolve failure records a ``failed`` outcome per account; comments-off records a
    ``comments_off`` outcome per account. Either way returns ``None`` so the caller
    skips the joins — one bad channel never aborts the campaign.
    """
    if not accounts:
        return None
    linked = await _safe_resolve(accounts[0], channel)
    if linked is None:
        if report:
            report(f"Ошибка: не удалось разрешить группу для {channel}")
        outcomes.extend(
            AccountChannelOnboarding(
                account_id=account_id, channel=channel, state="failed", reason="resolve_failed"
            )
            for account_id in accounts
        )
        return None
    if linked.comments_enabled and linked.linked_chat_id is not None:
        if report:
            report(f"Группа обсуждения для {channel} успешно разрешена")
        return linked.linked_chat_id
    if report:
        report(f"Канал {channel} отключил комментарии или не имеет группы обсуждения")
    outcomes.extend(
        AccountChannelOnboarding(account_id=account_id, channel=channel, state="comments_off")
        for account_id in accounts
    )
    return None


def _join_jitter_seconds() -> float:
    """Jittered anti-ban pause between discussion-group joins (config-driven)."""
    nc = settings.neurocomment
    return _seams.rng.uniform(nc.join_delay_min_seconds, nc.join_delay_max_seconds)


async def _join_pair_safely(
    account_id: str,
    channel: str,
    group_id: int,
    *,
    solver_enabled: bool,
) -> AccountChannelOnboarding:
    """Join one pair, converting an unexpected raise into a ``failed`` outcome."""
    try:
        return await _join_and_classify(
            account_id, channel, group_id, solver_enabled=solver_enabled
        )
    except Exception as exc:  # noqa: BLE001 - one pair must never abort the campaign
        await log_event(
            "ERROR",
            "neurocomment_onboard_pair_failed",
            account_id=account_id,
            extra={"channel": channel, "error_type": type(exc).__name__},
        )
        return AccountChannelOnboarding(
            account_id=account_id,
            channel=channel,
            state="failed",
            reason=type(exc).__name__,
        )


async def _probe_account_spam(
    accounts: list[str],
    report: Callable[[str], None] | None = None,
) -> None:
    """Probe each serving account's spam status once at onboarding (off the post path).

    Selection reads the cached verdict and never re-probes @SpamBot per post (anti-ban),
    so onboarding establishes one up front. ``force=False`` reuses a fresh cache; a probe
    failure is logged, never fatal — onboarding proceeds.
    """
    for account_id in accounts:
        if report:
            report(f"Проверка спам-статуса аккаунта {account_id}...")
        try:
            await _seams.refresh_spam_status(account_id, force=False)
        except Exception as exc:  # noqa: BLE001 - a spam probe must never abort onboarding
            await log_event(
                "WARNING",
                "neurocomment_onboard_spam_probe_failed",
                account_id=account_id,
                extra={"error_type": type(exc).__name__},
            )
            if report:
                report(f"Предупреждение: не удалось проверить спам-статус для {account_id}")
