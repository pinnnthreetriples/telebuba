"""Account-warming engine.

Pure business logic per non-negotiable #11: the warming algorithm, the
human-like pacing, the FloodWait handling, and the inter-account chat all live
here. NiceGUI handlers in ``features/warming.py`` are thin delegators; the same
functions could be driven from a CLI or a scheduler.

Design note — runtime model. Warming is a *continuous randomised loop* per
account (cycle → 12-30h sleep → repeat), not a fixed-schedule cron job, so each
running account owns an :class:`asyncio.Task` registered in ``_RUNTIME`` rather
than an APScheduler job. ``run_one_cycle`` is the testable unit; ``_warming_loop``
is the long-running wrapper around it.

Package layout. The cohesive, side-effect-light slices live in submodules —
``channels`` (input parsing), ``settings`` (settings row), ``board`` (kanban read
model), ``pacing`` (scheduling/intensity helpers). This module keeps the engine
itself: the state transitions, the cycle, the Gemini-driven chat, the quarantine
recovery, and the runtime loop — i.e. everything that calls the injectable seams
(``execute`` / ``generate_text`` / ``refresh_spam_status``), which tests patch on
this package namespace.

Telegram I/O only ever goes through ``core.telegram_client.execute`` with typed
actions; DB only through ``core.db``; HTTP only through ``core.gemini``.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from core.config import settings
from core.db import (
    count_pair_messages_since,
    fetch_account,
    fetch_warming_state,
    latest_unreplied_for,
    list_accounts,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    mark_message_replied,
    pair_key,
    record_dialogue_message,
    upsert_warming_state,
)
from core.gemini import generate_text
from core.logging import log_event
from core.telegram_client import execute
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import (
    JoinChannel,
    ReactToPost,
    ReadChannel,
    SendDirectMessage,
    SetOnline,
)
from schemas.warming import (
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingAccountState,
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
    is_warming,
    warming_health,
)
from services.content import is_acceptable, is_duplicate, register_sent
from services.dialogues import get_partners
from services.spam_status import refresh_spam_status
from services.warming.board import _to_card, load_board
from services.warming.channels import add_channels, list_channels, remove_channel
from services.warming.pacing import (
    _HALT_STATUSES,
    _SECONDS_PER_HOUR,
    _WAIT_STATUSES,
    _account_age_hours,
    _account_tz,
    _classify_flood,
    _in_quiet_hours,
    _local_now,
    _next_utc_midnight,
    _now_iso,
    _proxy_snapshot,
    _quiet_hours_end_at,
    _roll_daily,
    _seconds_until,
    _shift_to_active_hours,
    compute_intensity,
    evaluate_readiness,
)
from services.warming.settings_store import load_settings, save_settings

if TYPE_CHECKING:
    from schemas.accounts import AccountRead
    from schemas.dialogues import DialogueMessage
    from schemas.telegram_actions import ActionResult
    from schemas.warming import WarmingSettingsSecret

__all__ = [
    "UnknownAccountError",
    "WarmingNotReadyError",
    "add_channels",
    "compute_intensity",
    "evaluate_readiness",
    "list_channels",
    "load_board",
    "load_settings",
    "reconcile_warming_runtime",
    "remove_channel",
    "run_loop_iteration",
    "run_one_cycle",
    "save_settings",
    "shutdown_warming_runtime",
    "start_warming",
    "stop_warming",
]


class _Sentinel:
    """Marker type so ``_set_state`` can distinguish "carry current" from "set to None"."""


_SENTINEL: Final = _Sentinel()

# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs. Lives
# here (not in ``pacing``) so tests can patch ``warming._rng`` and have the
# engine's cycle/dialogue/timing draws and ``_human_delay`` all see it.
_rng = random.SystemRandom()

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

# Per-account async lock: prevents concurrent start/stop interleaving from
# leaving the DB and ``_RUNTIME`` in mismatched states. Locks are created lazily
# and never freed — the dictionary is bounded by the number of accounts.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}

# Control characters: strip from Gemini output before sending it as a DM.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_CHAT_PROMPTS = (
    "Напиши одно короткое дружелюбное сообщение для чата в Telegram (1-2 предложения), "
    "без хэштегов и без кавычек.",
    "Сгенерируй одну живую неформальную реплику для переписки в Telegram, "
    "максимум два предложения, без эмодзи-спама.",
    "Придумай короткое сообщение, как будто пишешь приятелю в Telegram. "
    "Только текст, без пояснений.",
)

_REPLY_PROMPT = (
    "Ответь коротко и по-дружески, как другу в Telegram, на это сообщение: "
    "«{incoming}». Только текст ответа, без кавычек."
)


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[account_id] = lock
    return lock


# --------------------------------------------------------------------------- #
# State transitions
# --------------------------------------------------------------------------- #


async def _set_state(  # noqa: PLR0913 - explicit state fields read clearer than a bag model here.
    account_id: str,
    state: WarmingState,
    *,
    last_event: str | None = None,
    last_cycle_at: str | None = None,
    next_run_at: str | None = None,
    increment_cycle: bool = False,
    last_error: str | None | _Sentinel = _SENTINEL,
    last_action: str | None | _Sentinel = _SENTINEL,
    last_channel: str | None | _Sentinel = _SENTINEL,
    heartbeat_at: str | None | _Sentinel = _SENTINEL,
    started_at: str | None | _Sentinel = _SENTINEL,
    stopped_at: str | None | _Sentinel = _SENTINEL,
    flood_wait_seconds: int | None | _Sentinel = _SENTINEL,
    flood_wait_until: str | None | _Sentinel = _SENTINEL,
    proxy_snapshot: str | None | _Sentinel = _SENTINEL,
    daily_actions: int | _Sentinel = _SENTINEL,
    daily_count_date: str | None | _Sentinel = _SENTINEL,
    quarantine_count: int | _Sentinel = _SENTINEL,
) -> WarmingStateRecord:
    current = await fetch_warming_state(account_id)
    cycles = current.cycles_completed if current else 0
    if increment_cycle:
        cycles += 1

    def _resolve(value: object, field: str) -> object:
        if value is _SENTINEL:
            return getattr(current, field) if current else None
        return value

    return await upsert_warming_state(
        WarmingStateWrite(
            account_id=account_id,
            state=state,
            cycles_completed=cycles,
            last_event=last_event if last_event is not None else _carry(current, "last_event"),
            last_cycle_at=(
                last_cycle_at if last_cycle_at is not None else _carry(current, "last_cycle_at")
            ),
            next_run_at=(
                next_run_at if next_run_at is not None else _carry(current, "next_run_at")
            ),
            last_error=cast("str | None", _resolve(last_error, "last_error")),
            last_action=cast("str | None", _resolve(last_action, "last_action")),
            last_channel=cast("str | None", _resolve(last_channel, "last_channel")),
            heartbeat_at=cast("str | None", _resolve(heartbeat_at, "heartbeat_at")),
            started_at=cast("str | None", _resolve(started_at, "started_at")),
            stopped_at=cast("str | None", _resolve(stopped_at, "stopped_at")),
            flood_wait_seconds=cast(
                "int | None",
                _resolve(flood_wait_seconds, "flood_wait_seconds"),
            ),
            flood_wait_until=cast(
                "str | None",
                _resolve(flood_wait_until, "flood_wait_until"),
            ),
            proxy_snapshot=cast("str | None", _resolve(proxy_snapshot, "proxy_snapshot")),
            daily_actions=cast("int", _resolve(daily_actions, "daily_actions") or 0),
            daily_count_date=cast("str | None", _resolve(daily_count_date, "daily_count_date")),
            quarantine_count=cast("int", _resolve(quarantine_count, "quarantine_count") or 0),
        ),
    )


def _carry(record: WarmingStateRecord | None, field: str) -> str | None:
    if record is None:
        return None
    value = getattr(record, field)
    return value if isinstance(value, str) else None


async def _current_card(account_id: str) -> WarmingAccountState:
    account = await fetch_account(account_id)
    record = await fetch_warming_state(account_id)
    if account is not None:
        return _to_card(account, record)
    state: WarmingState = record.state if record else "idle"
    return WarmingAccountState(
        account_id=account_id,
        label=account_id,
        state=state,
        health=warming_health(state),
        cycles_completed=record.cycles_completed if record else 0,
        last_event=record.last_event if record else None,
        last_cycle_at=record.last_cycle_at if record else None,
        next_run_at=record.next_run_at if record else None,
        updated_at=record.updated_at if record else None,
        last_error=record.last_error if record else None,
        last_action=record.last_action if record else None,
        last_channel=record.last_channel if record else None,
        heartbeat_at=record.heartbeat_at if record else None,
        started_at=record.started_at if record else None,
        stopped_at=record.stopped_at if record else None,
        flood_wait_seconds=record.flood_wait_seconds if record else None,
        flood_wait_until=record.flood_wait_until if record else None,
        proxy_snapshot=record.proxy_snapshot if record else None,
        daily_actions=record.daily_actions if record else 0,
        daily_count_date=record.daily_count_date if record else None,
        quarantine_count=record.quarantine_count if record else 0,
    )


# --------------------------------------------------------------------------- #
# Start / stop
# --------------------------------------------------------------------------- #


class UnknownAccountError(ValueError):
    """Raised when start/stop is called for an account that does not exist."""


class WarmingNotReadyError(ValueError):
    """Raised when ``start_warming`` refuses a not-ready account.

    Carries the structured ``reasons`` so the UI can show them to the user.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons) or "account not ready")


async def start_warming(data: StartWarmingRequest) -> WarmingAccountState:
    """Move an account into the warming column and kick off its loop task."""
    async with _account_lock(data.account_id):
        account = await fetch_account(data.account_id)
        if account is None:
            msg = f"Unknown account: {data.account_id}"
            raise UnknownAccountError(msg)
        if (await load_warming_settings()).enforce_readiness:
            channel_count = len((await list_warming_channels()).channels)
            readiness = evaluate_readiness(account, channel_count)
            if not readiness.ready:
                await log_event(
                    "WARNING",
                    "warming_start_blocked",
                    account_id=data.account_id,
                    extra={"reasons": readiness.reasons},
                )
                raise WarmingNotReadyError(readiness.reasons)
        await _set_state(
            data.account_id,
            "active",
            last_event="queued",
            started_at=_now_iso(),
            stopped_at=None,
            last_error=None,
            flood_wait_seconds=None,
            flood_wait_until=None,
            proxy_snapshot=_proxy_snapshot(account),
        )
        existing = _RUNTIME.get(data.account_id)
        if existing is None or existing.done():
            _RUNTIME[data.account_id] = asyncio.create_task(_warming_loop(data.account_id))
    await log_event("INFO", "warming_started", account_id=data.account_id)
    return await _current_card(data.account_id)


async def stop_warming(data: StopWarmingRequest) -> WarmingAccountState:
    """Cancel an account's loop task and return it to the idle column.

    Awaits the task with a timeout so callers get back a settled state — a UI
    poll that re-reads the board will see a real ``idle`` row, not a still-
    running shadow loop. Stopping a ghost account (no row in ``accounts``) is
    a no-op for the DB — only the in-memory task is cleaned up.
    """
    async with _account_lock(data.account_id):
        task = _RUNTIME.pop(data.account_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(task),
                    timeout=settings.warming.stop_cancel_timeout_seconds,
                )
            except (TimeoutError, asyncio.CancelledError):
                # Either we timed out or the cancel propagated correctly —
                # in both cases the task is no longer ours to await.
                pass
            except Exception as exc:  # noqa: BLE001 - log+continue; stop must not fail.
                await log_event(
                    "WARNING",
                    "warming_stop_task_error",
                    account_id=data.account_id,
                    extra={"error_type": type(exc).__name__, "message": str(exc)},
                )
        account = await fetch_account(data.account_id)
        if account is not None:
            await _set_state(
                data.account_id,
                "idle",
                last_event="stopped",
                stopped_at=_now_iso(),
            )
    await log_event("INFO", "warming_stopped", account_id=data.account_id)
    return await _current_card(data.account_id)


async def reconcile_warming_runtime() -> None:
    """Re-attach loop tasks for accounts whose DB state says they were running.

    ``_RUNTIME`` lives in process memory: after a restart the DB still shows
    ``active``/``sleeping``/``flood_wait`` but no task exists. We restart the
    loop for each such account so the board does not lie.
    """
    records = await list_warming_states()
    restarted = 0
    for record in records:
        if not is_warming(record.state):
            continue
        existing = _RUNTIME.get(record.account_id)
        if existing is not None and not existing.done():
            continue
        account = await fetch_account(record.account_id)
        if account is None:
            # Orphan state row — mark it stopped so the board is honest.
            await _set_state(
                record.account_id,
                "idle",
                last_event="reconcile_orphan",
                stopped_at=_now_iso(),
            )
            continue
        _RUNTIME[record.account_id] = asyncio.create_task(_warming_loop(record.account_id))
        restarted += 1
    if restarted:
        await log_event(
            "INFO",
            "warming_runtime_reconciled",
            extra={"restarted": restarted},
        )


async def shutdown_warming_runtime() -> None:
    """Cancel every running loop and wait briefly for graceful exits."""
    if not _RUNTIME:
        return
    tasks = list(_RUNTIME.values())
    _RUNTIME.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=settings.warming.stop_cancel_timeout_seconds,
        )
    except TimeoutError:
        await log_event("WARNING", "warming_shutdown_timeout", extra={"count": len(tasks)})


# --------------------------------------------------------------------------- #
# Warming cycle
# --------------------------------------------------------------------------- #


def _human_delay(min_seconds: float, max_seconds: float) -> float:
    """A human-like pause in ``[min, max]`` from a clipped log-normal.

    Real users are bursty: many short gaps with a heavy tail of long ones. We
    draw a log-normal fraction (median below the midpoint, occasional spike to
    the max) and map it onto the configured range — unlike a uniform draw, which
    is the most obvious bot signature.
    """
    lo, hi = sorted((min_seconds, max_seconds))
    if hi <= lo:
        return lo
    warm = settings.warming
    fraction = min(1.0, _rng.lognormvariate(warm.delay_lognorm_mu, warm.delay_lognorm_sigma))
    return lo + fraction * (hi - lo)


async def _human_pause(min_seconds: float, max_seconds: float) -> None:
    await asyncio.sleep(_human_delay(min_seconds, max_seconds))


async def _read_and_react(
    account_id: str,
    channel: str,
    *,
    reactions_enabled: bool,
    reaction_probability: float,
) -> tuple[int, int, ActionResult | None, int]:
    """Read a channel and maybe react. Returns (reads, reactions, flood_result, failures)."""
    warm = settings.warming
    reads = reactions = failures = 0
    read_result = await execute(
        account_id,
        ReadChannel(channel=channel, message_limit=warm.read_message_limit),
    )
    if read_result.status == "ok":
        reads = 1
    elif read_result.status == "failed":
        failures += 1
    elif read_result.status in _HALT_STATUSES:
        return reads, reactions, read_result, failures
    await _human_pause(warm.reading_min_seconds, warm.reading_max_seconds)
    if reactions_enabled and _rng.random() < reaction_probability:
        react_result = await execute(
            account_id,
            ReactToPost(
                channel=channel,
                reactions=warm.default_reactions,
                message_limit=warm.reaction_message_limit,
            ),
        )
        if react_result.status in _HALT_STATUSES:
            return reads, reactions, react_result, failures
        if react_result.status == "ok":
            reactions = 1
        elif react_result.status == "failed":
            failures += 1
    return reads, reactions, None, failures


async def run_one_cycle(  # noqa: C901, PLR0912, PLR0915 - linear cycle; splitting hides flow.
    data: WarmingCycleRequest,
) -> WarmingCycleResult:
    """Perform exactly one warming pass for an account. The testable core."""
    account_id = data.account_id
    secret = await load_warming_settings()
    channels = (await list_warming_channels()).channels
    if not channels:
        await log_event("WARNING", "warming_no_channels", account_id=account_id)
        return WarmingCycleResult(
            account_id=account_id,
            status="skipped",
            detail="no channels configured",
        )

    warm = settings.warming
    account = await fetch_account(account_id)
    intensity = compute_intensity(_account_age_hours(account, datetime.now(UTC)))
    online_set = False
    try:
        await execute(account_id, SetOnline(online=True))
        online_set = True
        await _human_pause(warm.typing_min_seconds, warm.typing_max_seconds)

        upper = min(intensity.channels_max, len(channels))
        lower = min(intensity.channels_min, upper)
        chosen = _rng.sample(channels, _rng.randint(lower, upper))

        joined = reads = reactions = failures = 0
        flood_seconds: int | None = None
        flood_until: str | None = None
        last_failed_action: str | None = None
        last_failed_channel: str | None = None
        flooded = False
        peer_flooded = False

        for channel in chosen:
            if secret.join_enabled:
                join_result = await execute(account_id, JoinChannel(channel=channel.channel))
                if join_result.status == "ok":
                    joined += 1
                elif join_result.status == "failed":
                    failures += 1
                    last_failed_action = "join"
                    last_failed_channel = channel.channel
                elif join_result.status == "peer_flood":
                    peer_flooded = True
                    last_failed_action = "join"
                    last_failed_channel = channel.channel
                    break
                elif join_result.status in _WAIT_STATUSES:
                    flooded, flood_seconds, flood_until = _classify_flood(join_result)
                    last_failed_action = "join"
                    last_failed_channel = channel.channel
                    break
                await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)

            (
                channel_reads,
                channel_reactions,
                channel_flood,
                channel_failures,
            ) = await _read_and_react(
                account_id,
                channel.channel,
                reactions_enabled=secret.reactions_enabled,
                reaction_probability=intensity.reaction_probability,
            )
            reads += channel_reads
            reactions += channel_reactions
            failures += channel_failures
            if channel_failures:
                last_failed_action = "read_or_react"
                last_failed_channel = channel.channel
            if channel_flood is not None:
                if channel_flood.status == "peer_flood":
                    peer_flooded = True
                else:
                    flooded, flood_seconds, flood_until = _classify_flood(channel_flood)
                last_failed_action = channel_flood.action_type
                last_failed_channel = channel.channel
                break
            await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)

        messages_sent = 0
        if (
            not flooded
            and not peer_flooded
            and intensity.dm_allowed
            and secret.inter_account_chat
            and secret.gemini_api_key
        ):
            messages_sent = await _maybe_inter_account_chat(account_id, secret)
    finally:
        # SetOnline(False) must run even if any of the inner steps raises so the
        # account does not stay online forever.
        if online_set:
            try:
                await execute(account_id, SetOnline(online=False))
            except Exception as exc:  # noqa: BLE001 - cleanup must never raise.
                await log_event(
                    "WARNING",
                    "warming_set_offline_failed",
                    account_id=account_id,
                    extra={"error_type": type(exc).__name__, "message": str(exc)},
                )

    if peer_flooded:
        status = "peer_flood"
    elif flooded:
        status = "flood_wait"
    elif failures and not (joined or reads or reactions):
        # Every action failed → don't lie about success.
        status = "failed"
    else:
        status = "ok"

    result = WarmingCycleResult(
        account_id=account_id,
        status=status,
        channels_joined=joined,
        channels_read=reads,
        reactions_sent=reactions,
        messages_sent=messages_sent,
        flood_wait_seconds=flood_seconds,
        flood_wait_until=flood_until,
        failures=failures,
        last_failed_action=last_failed_action,
        last_failed_channel=last_failed_channel,
    )
    await log_event(
        "WARNING" if status != "ok" else "INFO",
        "warming_cycle_completed",
        account_id=account_id,
        extra={
            "status": status,
            "joined": joined,
            "reads": reads,
            "reactions": reactions,
            "messages": messages_sent,
            "failures": failures,
            "flood_wait_seconds": flood_seconds,
        },
    )
    return result


# --------------------------------------------------------------------------- #
# Gemini-driven inter-account chat
# --------------------------------------------------------------------------- #


def _sanitize_chat_text(raw: str) -> str | None:
    """Strip control chars, trim, enforce length / line limits. ``None`` if empty."""
    cleaned = _CONTROL_CHARS_RE.sub("", raw).strip()
    if not cleaned:
        return None
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    lines = lines[: settings.warming.chat_message_max_lines]
    cleaned = "\n".join(lines)
    if len(cleaned) > settings.warming.chat_message_max_chars:
        cleaned = cleaned[: settings.warming.chat_message_max_chars].rstrip()
    return cleaned or None


async def _generate_chat_text(
    sender_id: str,
    secret: WarmingSettingsSecret,
    *,
    prompt: str | None = None,
) -> str | None:
    """Generate a chat line, retrying until it passes the filter and dedup.

    ``prompt`` overrides the random opener (used for context-aware replies).
    Returns ``None`` if generation fails outright or no acceptable, non-duplicate
    text is produced within ``content_max_attempts`` tries.
    """
    for _ in range(settings.warming.content_max_attempts):
        generated = await generate_text(
            GeminiRequest(
                api_key=secret.gemini_api_key,
                prompt=prompt or _rng.choice(_CHAT_PROMPTS),
                model=secret.gemini_model,
                temperature=settings.gemini.temperature,
                max_output_tokens=settings.gemini.max_output_tokens,
            ),
        )
        if generated.status != "ok" or not generated.text:
            await log_event(
                "WARNING",
                "warming_chat_generation_failed",
                account_id=sender_id,
                extra={"error": generated.error},
            )
            return None
        candidate = _sanitize_chat_text(generated.text)
        if candidate is None:
            continue
        if not is_acceptable(candidate):
            await log_event("INFO", "warming_chat_filtered", account_id=sender_id)
            continue
        if await is_duplicate(candidate):
            await log_event("INFO", "warming_chat_duplicate", account_id=sender_id)
            continue
        return candidate
    return None


async def _maybe_inter_account_chat(
    sender_id: str,
    secret: WarmingSettingsSecret,
) -> int:
    """Advance one dialogue turn for ``sender_id`` with one of its partners.

    Replies to the most recent unanswered message from a partner; otherwise
    opens a new conversation with an eligible partner. Returns messages sent.
    """
    partners = await get_partners(sender_id)
    if not partners:
        return 0
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}

    incoming = await latest_unreplied_for(sender_id)
    if incoming is not None and incoming.from_account in partners:
        return await _reply_to_partner(sender_id, incoming, secret, accounts)
    return await _open_with_partner(sender_id, partners, secret, accounts)


async def _reply_to_partner(
    sender_id: str,
    incoming: DialogueMessage,
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> int:
    target = accounts.get(incoming.from_account)
    if target is None or target.user_id is None:
        await mark_message_replied(incoming.id)
        return 0
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
        return 0
    text = await _generate_chat_text(
        sender_id,
        secret,
        prompt=_REPLY_PROMPT.format(incoming=incoming.text),
    )
    if text is None:
        return 0
    result = await execute(sender_id, SendDirectMessage(user_id=target.user_id, text=text))
    if result.status != "ok":
        return 0
    await mark_message_replied(incoming.id)
    await register_sent(text)
    # Chain: record our reply as a new pending message so the partner can answer
    # next cycle — this is what turns a single round-trip into a conversation.
    await record_dialogue_message(sender_id, incoming.from_account, text)
    await log_event(
        "INFO",
        "warming_dialogue_reply",
        account_id=sender_id,
        extra={"to": incoming.from_account},
    )
    return 1


async def _conversation_faded(account_a: str, account_b: str) -> bool:
    """True once a pair has exchanged ``dialogue_max_turns`` within the window."""
    warm = settings.warming
    since = (
        datetime.now(UTC) - timedelta(hours=warm.dialogue_conversation_window_hours)
    ).isoformat()
    count = await count_pair_messages_since(pair_key(account_a, account_b), since)
    return count >= warm.dialogue_max_turns


async def _open_with_partner(
    sender_id: str,
    partners: list[str],
    secret: WarmingSettingsSecret,
    accounts: dict[str, AccountRead],
) -> int:
    candidates = [
        accounts[partner]
        for partner in partners
        if accounts.get(partner) is not None and accounts[partner].user_id is not None
    ]
    if not candidates:
        return 0
    target = _rng.choice(candidates)
    if target.user_id is None:
        return 0
    text = await _generate_chat_text(sender_id, secret)
    if text is None:
        return 0
    result = await execute(sender_id, SendDirectMessage(user_id=target.user_id, text=text))
    if result.status != "ok":
        return 0
    await register_sent(text)
    await record_dialogue_message(sender_id, target.account_id, text)
    await log_event(
        "INFO",
        "warming_dialogue_opened",
        account_id=sender_id,
        extra={"to": target.account_id},
    )
    return 1


# --------------------------------------------------------------------------- #
# Long-running loop
# --------------------------------------------------------------------------- #


async def _recover_from_quarantine(
    account_id: str,
    record: WarmingStateRecord,
    now: datetime,
) -> WarmingCycleResult:
    """Re-check a quarantined account: resume if cleared, escalate otherwise.

    Called when a quarantine window has elapsed. Re-probes @SpamBot; a cleared
    account returns to warming, a still-limited one is re-quarantined until the
    configured repeat cap, after which it is given up on (error + alert).
    """
    warm = settings.warming
    verdict = await refresh_spam_status(account_id, force=True)
    if verdict.status != "limited":
        next_run = (now + timedelta(seconds=warm.startup_jitter_max_seconds)).isoformat()
        await _set_state(
            account_id,
            "sleeping",
            last_event="quarantine_recovered",
            next_run_at=next_run,
            heartbeat_at=now.isoformat(),
            last_error=None,
            quarantine_count=0,
        )
        await log_event("INFO", "warming_quarantine_recovered", account_id=account_id)
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="recovered")

    count = record.quarantine_count + 1
    if count >= warm.quarantine_max_repeats:
        await _set_state(
            account_id,
            "error",
            last_event="quarantine_exhausted",
            last_error=f"peer-flood not lifted after {count} checks",
            heartbeat_at=now.isoformat(),
            quarantine_count=count,
        )
        await log_event(
            "ERROR",
            "warming_quarantine_exhausted",
            account_id=account_id,
            extra={"checks": count},
        )
        return WarmingCycleResult(
            account_id=account_id,
            status="error",
            detail="quarantine exhausted",
        )

    next_run = (now + timedelta(hours=warm.quarantine_hours)).isoformat()
    await _set_state(
        account_id,
        "quarantine",
        last_event="quarantine_extended",
        next_run_at=next_run,
        heartbeat_at=now.isoformat(),
        quarantine_count=count,
    )
    await log_event(
        "WARNING",
        "warming_quarantine_extended",
        account_id=account_id,
        extra={"checks": count},
    )
    return WarmingCycleResult(account_id=account_id, status="skipped", detail="quarantine extended")


async def run_loop_iteration(account_id: str) -> WarmingCycleResult:  # noqa: C901 - linear loop step
    """Run one iteration of the warming loop (cycle + state transitions).

    Extracted from ``_warming_loop`` so it can be tested without the infinite
    ``while True`` wrapper. Updates DB state but does NOT sleep — it writes
    ``next_run_at``, the single source of truth the loop reads to time the next
    cycle (so a restart resumes the existing schedule instead of firing early).

    Two gates run before the cycle: quiet hours (park until the window ends) and
    the per-account daily action budget (park until UTC midnight).
    """
    now = datetime.now(UTC)
    warm = settings.warming
    controls = await load_warming_settings()
    record = await fetch_warming_state(account_id)

    if record is not None and record.state == "quarantine":
        return await _recover_from_quarantine(account_id, record, now)

    if controls.quiet_hours_enabled:
        local_now = await _local_now(account_id, now)
        if _in_quiet_hours(local_now, controls.quiet_hours_start, controls.quiet_hours_end):
            next_run = _quiet_hours_end_at(local_now, controls.quiet_hours_end).isoformat()
            await _set_state(
                account_id,
                "sleeping",
                last_event="quiet_hours",
                next_run_at=next_run,
                heartbeat_at=now.isoformat(),
            )
            return WarmingCycleResult(account_id=account_id, status="skipped", detail="quiet hours")

    daily_count, daily_date = _roll_daily(record, now.date().isoformat())
    if controls.max_daily_actions > 0 and daily_count >= controls.max_daily_actions:
        next_run = _next_utc_midnight(now).isoformat()
        await _set_state(
            account_id,
            "sleeping",
            last_event="daily_limit",
            next_run_at=next_run,
            heartbeat_at=now.isoformat(),
            daily_actions=daily_count,
            daily_count_date=daily_date,
        )
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="daily limit")

    await _set_state(
        account_id,
        "active",
        last_event="cycle_started",
        heartbeat_at=now.isoformat(),
        last_error=None,
        daily_actions=daily_count,
        daily_count_date=daily_date,
    )
    result = await run_one_cycle(WarmingCycleRequest(account_id=account_id))

    actions_done = (
        result.channels_joined + result.channels_read + result.reactions_sent + result.messages_sent
    )
    new_daily = daily_count + actions_done

    if result.status == "peer_flood":
        sleep_seconds = warm.quarantine_hours * _SECONDS_PER_HOUR
    elif result.status == "flood_wait" and result.flood_wait_seconds:
        sleep_seconds = float(result.flood_wait_seconds)
    else:
        sleep_seconds = _rng.uniform(
            warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
            warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
        )
    next_run_dt = datetime.now(UTC) + timedelta(seconds=sleep_seconds)
    # Bias a normal cadence to wake in active local hours; never delay a
    # flood/peer-flood recovery, which has its own required timing.
    if result.status not in {"peer_flood", "flood_wait"}:
        next_run_dt = _shift_to_active_hours(next_run_dt, await _account_tz(account_id))
    next_run = next_run_dt.isoformat()

    next_state: WarmingState
    if result.status == "peer_flood":
        next_state = "quarantine"
    elif result.status == "flood_wait":
        next_state = "flood_wait"
    elif result.status == "failed":
        next_state = "error"
    else:
        next_state = "sleeping"

    await _set_state(
        account_id,
        next_state,
        last_event=f"cycle:{result.status}",
        last_cycle_at=_now_iso(),
        next_run_at=next_run,
        increment_cycle=True,
        heartbeat_at=_now_iso(),
        last_action=result.last_failed_action,
        last_channel=result.last_failed_channel,
        last_error=result.detail,
        flood_wait_seconds=result.flood_wait_seconds,
        flood_wait_until=result.flood_wait_until,
        daily_actions=new_daily,
        daily_count_date=daily_date,
    )
    return result


def _loop_sleep_seconds(record: WarmingStateRecord | None, now: datetime) -> float:
    """Seconds to wait before the next cycle, from the persisted ``next_run_at``.

    Falls back to a fresh randomised 12-30h sleep only if the schedule is missing
    (it never should be after ``run_loop_iteration`` writes one).
    """
    if record is not None and record.next_run_at is not None:
        return _seconds_until(record.next_run_at, now)
    warm = settings.warming
    return _rng.uniform(
        warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
        warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
    )


def _initial_delay_seconds(record: WarmingStateRecord | None, now: datetime) -> float:
    """Delay before the first cycle after (re)starting a loop.

    Honours a persisted future ``next_run_at`` so a restart resumes the existing
    schedule; a fresh account (no schedule yet) only waits a short startup jitter.
    """
    if record is not None and record.next_run_at is not None:
        return _seconds_until(record.next_run_at, now)
    return _rng.uniform(0.0, settings.warming.startup_jitter_max_seconds)


async def _warming_loop(account_id: str) -> None:  # pragma: no cover - long-running task
    """Run cycles forever, timing each from the persisted ``next_run_at``.

    Never raises to the caller. On (re)start it respects an existing schedule so
    an app restart does not turn parked accounts into an activity spike.
    """
    try:
        record = await fetch_warming_state(account_id)
        await asyncio.sleep(_initial_delay_seconds(record, datetime.now(UTC)))
        while True:
            await run_loop_iteration(account_id)
            record = await fetch_warming_state(account_id)
            await asyncio.sleep(_loop_sleep_seconds(record, datetime.now(UTC)))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a background loop must never crash silently.
        await log_event(
            "ERROR",
            "warming_loop_crashed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )
        await _set_state(
            account_id,
            "error",
            last_event="loop_crashed",
            last_error=f"{type(exc).__name__}: {exc}",
            heartbeat_at=_now_iso(),
        )
