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

Telegram I/O only ever goes through ``core.telegram_client.execute`` with typed
actions; DB only through ``core.db``; HTTP only through ``core.gemini``.
"""

from __future__ import annotations

import asyncio
import random
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    add_warming_channel,
    fetch_account,
    fetch_warming_state,
    list_accounts,
    list_warming_channels,
    list_warming_states,
    load_warming_settings,
    remove_warming_channel,
    save_warming_settings,
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
    AddChannelsRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingAccountState,
    WarmingBoardState,
    WarmingChannelList,
    WarmingCycleRequest,
    WarmingCycleResult,
    WarmingSettings,
    WarmingSettingsSecret,
    WarmingSettingsUpdate,
    WarmingState,
    WarmingStateRecord,
    WarmingStateWrite,
    is_warming,
    warming_health,
)

if TYPE_CHECKING:
    from schemas.accounts import AccountRead

# SystemRandom: non-cryptographic jitter/selection; avoids ruff S311 on the
# module-level ``random.*`` helpers. Behaviour is identical for our needs.
_rng = random.SystemRandom()

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

_SECONDS_PER_HOUR = 3600
_CHAT_PROMPTS = (
    "Напиши одно короткое дружелюбное сообщение для чата в Telegram (1-2 предложения), "
    "без хэштегов и без кавычек.",
    "Сгенерируй одну живую неформальную реплику для переписки в Telegram, "
    "максимум два предложения, без эмодзи-спама.",
    "Придумай короткое сообщение, как будто пишешь приятелю в Telegram. "
    "Только текст, без пояснений.",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


def _normalize_channel(token: str) -> str | None:
    cleaned = token.strip().strip("<>").rstrip("/")
    if not cleaned:
        return None
    lowered = cleaned.lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if lowered.startswith(prefix):
            return cleaned[len(prefix) :] or None
    return cleaned


def _parse_channels(raw: str) -> list[str]:
    seen: list[str] = []
    lowered_seen: set[str] = set()
    for token in re.split(r"[\s,]+", raw.strip()):
        normalized = _normalize_channel(token)
        if normalized is None:
            continue
        key = normalized.lower()
        if key in lowered_seen:
            continue
        lowered_seen.add(key)
        seen.append(normalized)
    return seen


async def list_channels() -> WarmingChannelList:
    return await list_warming_channels()


async def add_channels(request: AddChannelsRequest) -> WarmingChannelList:
    """Parse a free-form blob of links/usernames and persist each unique one."""
    parsed = _parse_channels(request.raw)
    channels = await list_warming_channels()
    for channel in parsed:
        channels = await add_warming_channel(channel)
    await log_event("INFO", "warming_channels_added", extra={"count": len(parsed)})
    return channels


async def remove_channel(request: RemoveChannelRequest) -> WarmingChannelList:
    channels = await remove_warming_channel(request.channel)
    await log_event("INFO", "warming_channel_removed", extra={"channel": request.channel})
    return channels


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def _mask_settings(secret: WarmingSettingsSecret) -> WarmingSettings:
    return WarmingSettings(
        inter_account_chat=secret.inter_account_chat,
        reactions_enabled=secret.reactions_enabled,
        has_gemini_key=bool(secret.gemini_api_key),
        gemini_model=secret.gemini_model,
        updated_at=secret.updated_at,
    )


async def load_settings() -> WarmingSettings:
    return _mask_settings(await load_warming_settings())


async def save_settings(request: WarmingSettingsUpdate) -> WarmingSettings:
    secret = await save_warming_settings(
        inter_account_chat=request.inter_account_chat,
        reactions_enabled=request.reactions_enabled,
        gemini_api_key=request.gemini_api_key,
    )
    await log_event(
        "INFO",
        "warming_settings_saved",
        extra={
            "inter_account_chat": secret.inter_account_chat,
            "reactions_enabled": secret.reactions_enabled,
            "has_gemini_key": bool(secret.gemini_api_key),
        },
    )
    return _mask_settings(secret)


# --------------------------------------------------------------------------- #
# Board / kanban
# --------------------------------------------------------------------------- #


def _to_card(account: AccountRead, record: WarmingStateRecord | None) -> WarmingAccountState:
    state: WarmingState = record.state if record else "idle"
    return WarmingAccountState(
        account_id=account.account_id,
        label=account.label or account.account_id,
        state=state,
        health=warming_health(state),
        cycles_completed=record.cycles_completed if record else 0,
        last_event=record.last_event if record else None,
        last_cycle_at=record.last_cycle_at if record else None,
        next_run_at=record.next_run_at if record else None,
        updated_at=record.updated_at if record else None,
    )


async def load_board() -> WarmingBoardState:
    accounts = await list_accounts()
    records = {record.account_id: record for record in await list_warming_states()}
    channels = await list_warming_channels()
    masked = await load_settings()
    idle: list[WarmingAccountState] = []
    warming: list[WarmingAccountState] = []
    for account in accounts.accounts:
        card = _to_card(account, records.get(account.account_id))
        (warming if is_warming(card.state) else idle).append(card)
    return WarmingBoardState(
        idle=idle,
        warming=warming,
        channels=channels,
        settings=masked,
        channel_count=len(channels.channels),
        active_count=sum(1 for card in warming if card.state == "active"),
    )


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
) -> WarmingStateRecord:
    current = await fetch_warming_state(account_id)
    cycles = current.cycles_completed if current else 0
    if increment_cycle:
        cycles += 1
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
    )


# --------------------------------------------------------------------------- #
# Start / stop
# --------------------------------------------------------------------------- #


async def start_warming(request: StartWarmingRequest) -> WarmingAccountState:
    """Move an account into the warming column and kick off its loop task."""
    await _set_state(request.account_id, "active", last_event="queued")
    existing = _RUNTIME.get(request.account_id)
    if existing is None or existing.done():
        _RUNTIME[request.account_id] = asyncio.create_task(_warming_loop(request.account_id))
    await log_event("INFO", "warming_started", account_id=request.account_id)
    return await _current_card(request.account_id)


async def stop_warming(request: StopWarmingRequest) -> WarmingAccountState:
    """Cancel an account's loop task and return it to the idle column."""
    task = _RUNTIME.pop(request.account_id, None)
    if task is not None and not task.done():
        task.cancel()
    await _set_state(request.account_id, "idle", last_event="stopped")
    await log_event("INFO", "warming_stopped", account_id=request.account_id)
    return await _current_card(request.account_id)


# --------------------------------------------------------------------------- #
# Warming cycle
# --------------------------------------------------------------------------- #


async def _human_pause(min_seconds: float, max_seconds: float) -> None:
    await asyncio.sleep(_rng.uniform(min(min_seconds, max_seconds), max(min_seconds, max_seconds)))


async def _read_and_react(
    account_id: str,
    channel: str,
    *,
    reactions_enabled: bool,
) -> tuple[int, int, bool]:
    """Read a channel and maybe react. Returns (reads, reactions, flooded)."""
    warm = settings.warming
    reads = reactions = 0
    read_result = await execute(
        account_id,
        ReadChannel(channel=channel, message_limit=warm.read_message_limit),
    )
    if read_result.status == "ok":
        reads = 1
    await _human_pause(warm.reading_min_seconds, warm.reading_max_seconds)
    if reactions_enabled and _rng.random() < warm.reaction_probability:
        react_result = await execute(
            account_id,
            ReactToPost(
                channel=channel,
                reactions=warm.default_reactions,
                message_limit=warm.reaction_message_limit,
            ),
        )
        if react_result.status == "flood_wait":
            return reads, reactions, True
        if react_result.status == "ok":
            reactions = 1
    return reads, reactions, False


async def run_one_cycle(request: WarmingCycleRequest) -> WarmingCycleResult:
    """Perform exactly one warming pass for an account. The testable core."""
    account_id = request.account_id
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
    await execute(account_id, SetOnline(online=True))
    await _human_pause(warm.typing_min_seconds, warm.typing_max_seconds)

    upper = min(warm.channels_per_cycle_max, len(channels))
    lower = min(warm.channels_per_cycle_min, upper)
    chosen = _rng.sample(channels, _rng.randint(lower, upper))

    joined = reads = reactions = 0
    flooded = False
    for channel in chosen:
        join_result = await execute(account_id, JoinChannel(channel=channel.channel))
        if join_result.status == "flood_wait":
            flooded = True
            break
        if join_result.status == "ok":
            joined += 1
        await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
        channel_reads, channel_reactions, channel_flooded = await _read_and_react(
            account_id,
            channel.channel,
            reactions_enabled=secret.reactions_enabled,
        )
        reads += channel_reads
        reactions += channel_reactions
        if channel_flooded:
            flooded = True
            break
        await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)

    messages_sent = 0
    if not flooded and secret.inter_account_chat and secret.gemini_api_key:
        messages_sent = await _maybe_inter_account_chat(account_id, secret)

    await execute(account_id, SetOnline(online=False))
    status = "flood_wait" if flooded else "ok"
    result = WarmingCycleResult(
        account_id=account_id,
        status=status,
        channels_joined=joined,
        channels_read=reads,
        reactions_sent=reactions,
        messages_sent=messages_sent,
    )
    await log_event(
        "WARNING" if flooded else "INFO",
        "warming_cycle_completed",
        account_id=account_id,
        extra={
            "status": status,
            "joined": joined,
            "reads": reads,
            "reactions": reactions,
            "messages": messages_sent,
        },
    )
    return result


async def _maybe_inter_account_chat(sender_id: str, secret: WarmingSettingsSecret) -> int:
    """If two+ accounts are warming, send one Gemini-written DM to a peer."""
    records = await list_warming_states()
    peer_ids = {
        record.account_id
        for record in records
        if is_warming(record.state) and record.account_id != sender_id
    }
    if not peer_ids:
        return 0
    accounts = {account.account_id: account for account in (await list_accounts()).accounts}
    eligible = [
        account_id
        for account_id in peer_ids
        if accounts.get(account_id) is not None and accounts[account_id].user_id is not None
    ]
    if not eligible:
        return 0
    receiver_id = _rng.choice(eligible)
    receiver_user_id = accounts[receiver_id].user_id
    if receiver_user_id is None:
        return 0

    generated = await generate_text(
        GeminiRequest(
            api_key=secret.gemini_api_key,
            prompt=_rng.choice(_CHAT_PROMPTS),
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
        return 0

    result = await execute(
        sender_id,
        SendDirectMessage(user_id=receiver_user_id, text=generated.text),
    )
    if result.status != "ok":
        return 0
    await log_event(
        "INFO",
        "warming_chat_sent",
        account_id=sender_id,
        extra={"to": receiver_id},
    )
    return 1


async def _warming_loop(account_id: str) -> None:  # pragma: no cover - long-running task
    """Run cycles forever with 12-30h sleeps in between. Never raises to caller."""
    try:
        await _human_pause(0.0, settings.warming.startup_jitter_max_seconds)
        while True:
            await _set_state(account_id, "active", last_event="cycle_started")
            result = await run_one_cycle(WarmingCycleRequest(account_id=account_id))
            sleep_seconds = _rng.uniform(
                settings.warming.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
                settings.warming.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
            )
            next_run = (datetime.now(UTC) + timedelta(seconds=sleep_seconds)).isoformat()
            next_state: WarmingState = "flood_wait" if result.status == "flood_wait" else "sleeping"
            await _set_state(
                account_id,
                next_state,
                last_event=f"cycle:{result.status}",
                last_cycle_at=_now_iso(),
                next_run_at=next_run,
                increment_cycle=True,
            )
            await asyncio.sleep(sleep_seconds)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a background loop must never crash silently.
        await log_event(
            "ERROR",
            "warming_loop_crashed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )
        await _set_state(account_id, "error", last_event="loop_crashed")
