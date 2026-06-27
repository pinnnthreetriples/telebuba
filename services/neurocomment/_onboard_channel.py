"""Per-channel + per-pair onboarding helpers extracted from ``onboarding``.

Split out so :mod:`services.neurocomment.onboarding` stays under the aislop
file-size cap. The public entrypoint (``onboard_campaign``) still lives there
and only threads an :class:`OnboardContext` through these helpers.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING

from schemas.neurocomment import AccountChannelOnboarding

if TYPE_CHECKING:
    from collections.abc import Callable

# Status → operator-facing Russian label; the inner pair helper reports it.
_JOIN_STATUS_RU: dict[str, str] = {
    "ready": "готов",
    "comments_off": "комментарии отключены",
    "join_by_request": "отправлена заявка",
    "chat_restricted": "ограничен в записи",
    "joining": "ожидание лимитов",
    "bot_challenge_backoff": "пауза капчи",
    "bot_challenge": "требуется капча",
    "failed": "ошибка",
}


@dataclasses.dataclass(frozen=True, slots=True)
class OnboardContext:
    """Per-campaign onboarding state threaded through the channel + pair helpers.

    Packs the otherwise-many parameters (accounts, already_ready set, outcomes
    accumulator, solver flag, progress callbacks) into one value so the helpers
    stay under the PLR0913 argument-count limit.
    """

    accounts: list[str]
    already_ready: set[tuple[str, str]]
    outcomes: list[AccountChannelOnboarding]
    solver_enabled: bool
    on_progress: Callable[[str], None] | None
    report: Callable[[str], None]


async def onboard_channel(channel: str, ctx: OnboardContext, *, joined_once: bool) -> bool:
    """Onboard every account on one channel; return the updated ``joined_once`` flag.

    Compute the "remaining" account list = accounts NOT already ready for THIS
    channel. If every pair is ready, skip the Telegram resolve entirely (anti-ban
    + a fully-prepared channel costs zero reads). A transient resolve failure here
    would otherwise clobber the already-ready pairs with "failed" outcomes (Bug 3).
    """
    # Lazy import keeps this module free of ``onboarding`` at import time (the
    # parent module consumes this one's public API, so a top-level import would
    # cycle).
    from services.neurocomment import onboarding  # noqa: PLC0415

    remaining = [acc for acc in ctx.accounts if (acc, channel) not in ctx.already_ready]
    if not remaining:
        ctx.report(f"Канал {channel}: все пары уже готовы — пропуск.")
        ctx.outcomes.extend(
            AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
            for account_id in ctx.accounts
        )
        return joined_once
    ctx.report(f"Разрешение группы обсуждения для {channel}...")
    group_id = await onboarding._resolve_group_for_join(  # noqa: SLF001 - peer module
        remaining, channel, ctx.outcomes, report=ctx.on_progress
    )
    if group_id is None:
        ctx.outcomes.extend(
            AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
            for account_id in ctx.accounts
            if (account_id, channel) in ctx.already_ready
        )
        return joined_once
    for account_id in ctx.accounts:
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
        ctx.report(f"Аккаунт {account_id} уже готов для {channel} — пропуск.")
        ctx.outcomes.append(
            AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")
        )
        return joined_once
    if joined_once:
        jitter = onboarding._join_jitter_seconds()  # noqa: SLF001 - peer module
        ctx.report(f"Пауза {jitter:.1f} сек для обхода спам-фильтров...")
        await asyncio.sleep(jitter)
    ctx.report(f"Аккаунт {account_id}: вступление в группу для {channel}...")
    outcome = await onboarding._join_pair_safely(  # noqa: SLF001 - peer module
        account_id, channel, group_id, solver_enabled=ctx.solver_enabled
    )
    status_ru = _JOIN_STATUS_RU.get(outcome.state, outcome.state)
    reason_str = f" ({outcome.reason})" if outcome.reason else ""
    ctx.report(f"Результат для {account_id} на {channel}: {status_ru}{reason_str}")
    ctx.outcomes.append(outcome)
    return True
