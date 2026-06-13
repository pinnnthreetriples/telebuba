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
from typing import TYPE_CHECKING, Final, cast

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
    ActionResult,
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
    WarmingIntensity,
    WarmingReadiness,
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


class _Sentinel:
    """Marker type so ``_set_state`` can distinguish "carry current" from "set to None"."""


_SENTINEL: Final = _Sentinel()

# account_id -> running warming loop. Genuine runtime state (rare exception to
# the "no classes for stateless logic" rule): the loops must outlive a single
# UI handler call so the board can start/stop them.
_RUNTIME: dict[str, asyncio.Task[None]] = {}

# Per-account async lock: prevents concurrent start/stop interleaving from
# leaving the DB and ``_RUNTIME`` in mismatched states. Locks are created lazily
# and never freed — the dictionary is bounded by the number of accounts.
_ACCOUNT_LOCKS: dict[str, asyncio.Lock] = {}

_SECONDS_PER_HOUR = 3600
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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _account_lock(account_id: str) -> asyncio.Lock:
    lock = _ACCOUNT_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _ACCOUNT_LOCKS[account_id] = lock
    return lock


# --------------------------------------------------------------------------- #
# Scheduling helpers (pure) — quiet hours, daily budget, next-run timing.
# --------------------------------------------------------------------------- #


def _seconds_until(next_run_at_iso: str, now: datetime) -> float:
    """Seconds from ``now`` until an ISO timestamp, never negative.

    Corrupt/naive timestamps degrade to ``0.0`` so the loop runs now rather than
    crashing or sleeping forever.
    """
    try:
        target = datetime.fromisoformat(next_run_at_iso)
    except ValueError:
        return 0.0
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - now).total_seconds())


def _in_quiet_hours(now: datetime, start_hour: int, end_hour: int) -> bool:
    """True when ``now``'s UTC hour falls in the ``[start, end)`` window.

    ``start == end`` means "no window" (always False). The window wraps midnight
    when ``start > end`` (e.g. 23→7).
    """
    if start_hour == end_hour:
        return False
    hour = now.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _quiet_hours_end_at(now: datetime, end_hour: int) -> datetime:
    """The next UTC datetime at ``end_hour:00`` strictly after ``now``."""
    candidate = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_utc_midnight(now: datetime) -> datetime:
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _roll_daily(record: WarmingStateRecord | None, today: str) -> tuple[int, str]:
    """Return ``(count, date)`` for today, resetting the counter on a new day."""
    if record is None or record.daily_count_date != today:
        return 0, today
    return record.daily_actions, today


def _proxy_snapshot(account: AccountRead) -> str | None:
    """Freeze which proxy an account started warming with, for later diagnosis."""
    if not account.proxy_host:
        return None
    base = f"{account.proxy_type or 'proxy'}://{account.proxy_host}:{account.proxy_port}"
    if account.proxy_country_code:
        base = f"{base} ({account.proxy_country_code})"
    return base


def evaluate_readiness(account: AccountRead, channel_count: int) -> WarmingReadiness:
    """Decide whether an account can safely start warming, from last-known state.

    Uses the persisted account/proxy snapshot (no live network) so the board can
    show a badge cheaply and ``start_warming`` can refuse broken accounts.
    """
    reasons: list[str] = []
    if account.status != "alive":
        reasons.append(f"session {account.status}")
    if not account.proxy_host:
        reasons.append("no proxy")
    elif account.proxy_status == "failed":
        reasons.append("proxy failed")
    if channel_count <= 0:
        reasons.append("no channels")
    return WarmingReadiness(ready=not reasons, reasons=reasons)


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #

# Allowed token format for a Telegram channel/group identifier. We accept
# the canonical ``@username`` form and bare ``username`` / ``invite_hash``;
# the resolver in Telethon handles invite hashes (``joinchat/<hash>``).
_CHANNEL_TOKEN_RE = re.compile(r"^@?[A-Za-z0-9_]{3,32}(/[A-Za-z0-9_-]+)?$")
_INVITE_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _normalize_channel(token: str) -> str | None:
    cleaned = token.strip().strip("<>").rstrip("/")
    if not cleaned:
        return None
    lowered = cleaned.lower()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.lstrip("@")
    if not cleaned:
        return None
    if cleaned.startswith("+"):
        # Telegram private invite link of the form ``+abcDEF...``.
        cleaned = cleaned[1:]
        return cleaned if _INVITE_HASH_RE.match(cleaned) else None
    if cleaned.startswith("joinchat/"):
        invite = cleaned.split("/", 1)[1]
        return f"joinchat/{invite}" if _INVITE_HASH_RE.match(invite) else None
    if len(cleaned) > settings.warming.max_channel_length:
        return None
    return cleaned if _CHANNEL_TOKEN_RE.match(cleaned) else None


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


async def add_channels(data: AddChannelsRequest) -> WarmingChannelList:
    """Parse a free-form blob of links/usernames and persist each unique one.

    Enforces ``settings.warming.max_channels_per_add`` and
    ``settings.warming.max_channels_total`` — junk uploads cannot grow the table
    without bound.
    """
    parsed = _parse_channels(data.raw)
    if not parsed:
        return await list_warming_channels()

    warm = settings.warming
    parsed = parsed[: warm.max_channels_per_add]
    existing = await list_warming_channels()
    existing_keys = {ch.channel.lower() for ch in existing.channels}
    headroom = max(0, warm.max_channels_total - len(existing_keys))

    channels = existing
    added = 0
    for channel in parsed:
        if added >= headroom:
            await log_event(
                "WARNING",
                "warming_channel_limit_reached",
                extra={"limit": warm.max_channels_total},
            )
            break
        if channel.lower() in existing_keys:
            continue
        channels = await add_warming_channel(channel)
        existing_keys.add(channel.lower())
        added += 1
    await log_event(
        "INFO",
        "warming_channels_added",
        extra={"count": added, "submitted": len(parsed)},
    )
    return channels


async def remove_channel(data: RemoveChannelRequest) -> WarmingChannelList:
    channels = await remove_warming_channel(data.channel)
    await log_event("INFO", "warming_channel_removed", extra={"channel": data.channel})
    return channels


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def _mask_settings(secret: WarmingSettingsSecret) -> WarmingSettings:
    return WarmingSettings(
        inter_account_chat=secret.inter_account_chat,
        reactions_enabled=secret.reactions_enabled,
        join_enabled=secret.join_enabled,
        enforce_readiness=secret.enforce_readiness,
        quiet_hours_enabled=secret.quiet_hours_enabled,
        quiet_hours_start=secret.quiet_hours_start,
        quiet_hours_end=secret.quiet_hours_end,
        max_daily_actions=secret.max_daily_actions,
        has_gemini_key=bool(secret.gemini_api_key),
        gemini_model=secret.gemini_model,
        updated_at=secret.updated_at,
    )


async def load_settings() -> WarmingSettings:
    return _mask_settings(await load_warming_settings())


async def save_settings(data: WarmingSettingsUpdate) -> WarmingSettings:
    # ``clear_gemini_key`` wins over ``gemini_api_key``: the UI uses the flag
    # for an explicit "wipe the stored key" gesture; passing an empty string
    # also clears it; passing ``None`` (and no flag) preserves the existing key.
    if data.clear_gemini_key:
        api_key: str | None = ""
    else:
        api_key = data.gemini_api_key

    secret = await save_warming_settings(
        inter_account_chat=data.inter_account_chat,
        reactions_enabled=data.reactions_enabled,
        join_enabled=data.join_enabled,
        enforce_readiness=data.enforce_readiness,
        quiet_hours_enabled=data.quiet_hours_enabled,
        quiet_hours_start=data.quiet_hours_start,
        quiet_hours_end=data.quiet_hours_end,
        max_daily_actions=data.max_daily_actions,
        gemini_api_key=api_key,
        gemini_model=data.gemini_model,
    )
    await log_event(
        "INFO",
        "warming_settings_saved",
        extra={
            "inter_account_chat": secret.inter_account_chat,
            "reactions_enabled": secret.reactions_enabled,
            "join_enabled": secret.join_enabled,
            "enforce_readiness": secret.enforce_readiness,
            "quiet_hours_enabled": secret.quiet_hours_enabled,
            "max_daily_actions": secret.max_daily_actions,
            "has_gemini_key": bool(secret.gemini_api_key),
            "gemini_model": secret.gemini_model,
        },
    )
    return _mask_settings(secret)


# --------------------------------------------------------------------------- #
# Board / kanban
# --------------------------------------------------------------------------- #


def _to_card(
    account: AccountRead,
    record: WarmingStateRecord | None,
    *,
    readiness: WarmingReadiness | None = None,
) -> WarmingAccountState:
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
        readiness=readiness,
    )


async def load_board() -> WarmingBoardState:
    accounts = await list_accounts()
    records = {record.account_id: record for record in await list_warming_states()}
    channels = await list_warming_channels()
    masked = await load_settings()
    channel_count = len(channels.channels)
    idle: list[WarmingAccountState] = []
    warming: list[WarmingAccountState] = []
    for account in accounts.accounts:
        readiness = evaluate_readiness(account, channel_count)
        card = _to_card(account, records.get(account.account_id), readiness=readiness)
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


async def _human_pause(min_seconds: float, max_seconds: float) -> None:
    await asyncio.sleep(_rng.uniform(min(min_seconds, max_seconds), max(min_seconds, max_seconds)))


def _classify_flood(result: ActionResult) -> tuple[bool, int | None, str | None]:
    """Extract (flooded, seconds, until_iso) from an ActionResult."""
    if result.status != "flood_wait":
        return False, None, None
    seconds = result.flood_wait_seconds
    until = None
    if seconds is not None:
        until = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    return True, seconds, until


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
    elif read_result.status == "flood_wait":
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
        if react_result.status == "flood_wait":
            return reads, reactions, react_result, failures
        if react_result.status == "ok":
            reactions = 1
        elif react_result.status == "failed":
            failures += 1
    return reads, reactions, None, failures


def _account_age_hours(account: AccountRead | None, now: datetime) -> float:
    """Hours since the account was created; full-ramp age when unknown.

    A missing/unparseable ``created_at`` degrades to full intensity so an
    anomalous record never silently freezes an account at day-one behaviour.
    """
    if account is None:
        return settings.warming.ramp_full_age_hours
    try:
        created = datetime.fromisoformat(account.created_at)
    except ValueError:
        return settings.warming.ramp_full_age_hours
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(0.0, (now - created).total_seconds() / _SECONDS_PER_HOUR)


def compute_intensity(age_hours: float) -> WarmingIntensity:
    """Map an account's age to its per-cycle intensity via the configured ramp.

    Channels-per-cycle and reaction rate grow linearly from a quiet initial
    floor to the configured full values over ``ramp_full_age_hours``; DM is
    gated until ``dm_min_age_hours``. With the ramp disabled, every account runs
    at full intensity with DM allowed.
    """
    warm = settings.warming
    if not warm.ramp_enabled:
        return WarmingIntensity(
            channels_min=warm.channels_per_cycle_min,
            channels_max=warm.channels_per_cycle_max,
            reaction_probability=warm.reaction_probability,
            dm_allowed=True,
        )
    if warm.ramp_full_age_hours <= 0:
        frac = 1.0
    else:
        frac = min(1.0, max(0.0, age_hours / warm.ramp_full_age_hours))
    initial_channels = min(warm.ramp_initial_channels_max, warm.channels_per_cycle_max)
    grown = round(frac * (warm.channels_per_cycle_max - initial_channels))
    channels_max = max(1, initial_channels + grown)
    channels_min = min(warm.channels_per_cycle_min, channels_max)
    reaction_probability = warm.ramp_initial_reaction_probability + frac * (
        warm.reaction_probability - warm.ramp_initial_reaction_probability
    )
    return WarmingIntensity(
        channels_min=channels_min,
        channels_max=channels_max,
        reaction_probability=min(1.0, max(0.0, reaction_probability)),
        dm_allowed=age_hours >= warm.dm_min_age_hours,
    )


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

        for channel in chosen:
            if secret.join_enabled:
                join_result = await execute(account_id, JoinChannel(channel=channel.channel))
                if join_result.status == "ok":
                    joined += 1
                elif join_result.status == "failed":
                    failures += 1
                    last_failed_action = "join"
                    last_failed_channel = channel.channel
                elif join_result.status == "flood_wait":
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
                flooded, flood_seconds, flood_until = _classify_flood(channel_flood)
                last_failed_action = channel_flood.action_type
                last_failed_channel = channel.channel
                break
            await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)

        messages_sent = 0
        if (
            not flooded
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

    if flooded:
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


async def _maybe_inter_account_chat(  # noqa: PLR0911 - explicit early returns clearer than nesting.
    sender_id: str,
    secret: WarmingSettingsSecret,
) -> int:
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

    sanitized = _sanitize_chat_text(generated.text)
    if sanitized is None:
        await log_event(
            "WARNING",
            "warming_chat_text_empty_after_sanitize",
            account_id=sender_id,
        )
        return 0

    result = await execute(
        sender_id,
        SendDirectMessage(user_id=receiver_user_id, text=sanitized),
    )
    if result.status != "ok":
        return 0
    await log_event(
        "INFO",
        "warming_chat_sent",
        account_id=sender_id,
        extra={"to": receiver_id, "length": len(sanitized)},
    )
    return 1


# --------------------------------------------------------------------------- #
# Long-running loop
# --------------------------------------------------------------------------- #


async def run_loop_iteration(account_id: str) -> WarmingCycleResult:
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

    if controls.quiet_hours_enabled and _in_quiet_hours(
        now,
        controls.quiet_hours_start,
        controls.quiet_hours_end,
    ):
        next_run = _quiet_hours_end_at(now, controls.quiet_hours_end).isoformat()
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

    if result.status == "flood_wait" and result.flood_wait_seconds:
        sleep_seconds = float(result.flood_wait_seconds)
    else:
        sleep_seconds = _rng.uniform(
            warm.cycle_sleep_min_hours * _SECONDS_PER_HOUR,
            warm.cycle_sleep_max_hours * _SECONDS_PER_HOUR,
        )
    next_run = (datetime.now(UTC) + timedelta(seconds=sleep_seconds)).isoformat()

    next_state: WarmingState
    if result.status == "flood_wait":
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
