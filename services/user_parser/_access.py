"""How an account reaches a source: by its username, or — for an invite — from inside.

A public chat is read by its username, no join. A private invite chat needs membership,
and the operator's decision is to join it automatically — but only after asking whether
this account is already inside: Telegram's answer to a re-join is indistinguishable from
a join, and it would spend the day's join budget and leave a trace for nothing.

A join spends the fleet's one join budget (``neurocomment_join_log``) under the shared
join lock, with the per-account join pacer in front of it — the path
``services.chat_broadcast._join`` takes, because Telegram counts joins per ACCOUNT
whichever feature spends them. A chat the account cannot get into (the day's cap, a join
request, a ban, a dead link) is ``join_failed`` and the run goes on without it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.logging import log_event
from core.repositories.neurocomment import count_account_joins_since, record_join
from core.telegram_client import TelegramReadError
from schemas.telegram_action_results import ResolveChatResult
from schemas.telegram_actions import JoinChannel, ResolveChat
from services import pacing
from services._account_limits import account_join_cap
from services._join_lock import join_lock
from services.user_parser import _seams
from services.user_parser._streams import JobResult

if TYPE_CHECKING:
    from schemas.telegram_action_results import ActionResult
    from services.user_parser._context import RunContext, SourceState

_FLOOD_STATUSES = frozenset({"flood_wait", "peer_flood", "premium_wait", "slow_mode_wait"})
_JOINED_STATUSES = frozenset({"ok", "already_participant"})


def flood_cooldown(seconds: int | None) -> int:
    """A limit Telegram gave no duration for takes the configured cooldown."""
    return settings.user_parser.peer_flood_cooldown_seconds if seconds is None else seconds


def read_failure(exc: TelegramReadError) -> JobResult | None:
    """A rate limit or a dead connection as a retryable result; ``None`` for anything else."""
    if exc.kind == "flood_wait":
        return JobResult(flood_seconds=flood_cooldown(exc.seconds), retry=True)
    if exc.kind == "unavailable":
        return JobResult(unreachable=True, retry=True)
    return None


async def _resolve(account_id: str, token: str) -> ResolveChatResult | TelegramReadError:
    try:
        result = await _seams.execute_read(account_id, ResolveChat(target=token))
    except TelegramReadError as exc:
        return exc
    if not isinstance(result, ResolveChatResult):
        return TelegramReadError("unexpected_result")
    return result


async def _at_join_cap(account_id: str) -> bool:
    cap = await account_join_cap(account_id, settings.user_parser.max_joins_per_account_per_day)
    if cap <= 0:
        return False
    since = (_seams.now() - timedelta(days=1)).isoformat()
    return await count_account_joins_since(account_id, since) >= cap


def _join_pause() -> float:
    limits = settings.user_parser
    gap = limits.join_gap_seconds
    return pacing.human_delay(
        gap,
        gap * 2,
        rng=_seams.rng,
        mu=limits.join_lognorm_mu,
        sigma=limits.join_lognorm_sigma,
    )


async def _join_failed(ctx: RunContext, index: int, account_id: str, reason: str) -> JobResult:
    source = ctx.sources[index]
    source.report.status = "join_failed"
    await log_event(
        "WARNING",
        "user_parser_source_join_failed",
        account_id=account_id,
        extra={"run_id": ctx.run_id, "source": source.raw, "reason": reason},
    )
    return JobResult()


def _join_refusal(result: ActionResult) -> JobResult | None:
    """A join a rate limit or a dead connection stopped: retryable, on another account."""
    if result.status in _FLOOD_STATUSES:
        return JobResult(flood_seconds=flood_cooldown(result.flood_wait_seconds), retry=True)
    if result.status == "unavailable":
        return JobResult(unreachable=True, retry=True)
    return None


async def _capped(ctx: RunContext, index: int, account_id: str, attempt: int) -> JobResult:
    """This account's day of joins is spent: another account may still have room.

    The cap is the account's, not the chat's, so the first time it only hands the page
    on; the chat is written off once the retry hits a cap as well.
    """
    if attempt == 0:
        return JobResult(retry=True)
    return await _join_failed(ctx, index, account_id, "join_cap")


async def _join(
    ctx: RunContext, index: int, account_id: str, token: str, attempt: int
) -> str | JobResult:
    if await _at_join_cap(account_id):
        return await _capped(ctx, index, account_id, attempt)
    await _seams.await_send_slot(f"join:{account_id}", _join_pause())
    async with join_lock(account_id):
        if await _at_join_cap(account_id):
            return await _capped(ctx, index, account_id, attempt)
        result = await _seams.execute(account_id, JoinChannel(channel=token))
        if result.status == "ok":
            await record_join(account_id)
    refused = _join_refusal(result)
    if refused is not None:
        return refused
    if result.status not in _JOINED_STATUSES:
        return await _join_failed(ctx, index, account_id, result.error_type or result.status)
    inside = await _resolve(account_id, token)
    if isinstance(inside, TelegramReadError):
        return read_failure(inside) or await _join_failed(ctx, index, account_id, inside.reason)
    return str(inside.chat_id)


async def _reach_invite(
    ctx: RunContext, index: int, account_id: str, token: str, attempt: int
) -> str | JobResult:
    resolved = await _resolve(account_id, token)
    if isinstance(resolved, TelegramReadError):
        retry = read_failure(resolved)
        if retry is not None:
            return retry
        # ``chat_not_found``: the invite resolves only from inside, so this account is not.
        return await _join(ctx, index, account_id, token, attempt)
    return str(resolved.chat_id)


async def reach(ctx: RunContext, account_id: str, index: int, attempt: int = 0) -> str | JobResult:
    """The peer ``account_id`` reads source ``index`` by, or the result that ends the page."""
    known = ctx.access.get((account_id, index))
    if known is not None:
        return known
    source: SourceState = ctx.sources[index]
    token = source.token
    if token is None:
        source.failed = True
        return JobResult()
    if not source.invite:
        peer = token
    else:
        reached = await _reach_invite(ctx, index, account_id, token, attempt)
        if isinstance(reached, JobResult):
            return reached
        peer = reached
    ctx.access[(account_id, index)] = peer
    return peer
