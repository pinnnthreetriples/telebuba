"""The warming cycle — one human-like pass of reads / reactions / joins / chat.

``run_one_cycle`` is the testable core. Telegram and randomness are reached via
:mod:`services.warming._seams` so tests patch them in one place.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    is_channel_joined,
    list_warming_channels,
    load_warming_settings,
    record_channel_joined,
)
from core.logging import log_event
from schemas.telegram_actions import JoinChannel, ReactToPost, ReadChannel, SetOnline
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.warming import _seams
from services.warming._chat import _maybe_inter_account_chat
from services.warming.pacing import (
    _HALT_STATUSES,
    _WAIT_STATUSES,
    _account_age_hours,
    _classify_flood,
    compute_intensity,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.telegram_actions import ActionResult
    from schemas.warming import WarmingChannel, WarmingIntensity, WarmingSettingsSecret

    # Live progress hook: the loop passes a callback that persists the named
    # step (set_online/join/read/react/send_dm) so the board rail can advance
    # mid-cycle. None = no-op (every non-loop caller and most tests).
    _OnStep = Callable[[str], Awaitable[None]]


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
    fraction = min(1.0, _seams.rng.lognormvariate(warm.delay_lognorm_mu, warm.delay_lognorm_sigma))
    # min(hi, ...) guards the float-rounding edge where fraction == 1.0 makes
    # lo + (hi - lo) overshoot hi by an ULP — the result must stay within [lo, hi].
    return min(hi, lo + fraction * (hi - lo))


async def _human_pause(min_seconds: float, max_seconds: float) -> None:
    await asyncio.sleep(_human_delay(min_seconds, max_seconds))


async def _emit_step(on_step: _OnStep | None, step: str) -> None:
    """Fire the live-progress hook for ``step`` when the loop supplied one."""
    if on_step is not None:
        await on_step(step)


async def _read_and_react(  # noqa: PLR0913
    account_id: str,
    channel: str,
    *,
    reactions_enabled: bool,
    reaction_probability: float,
    attempts_so_far: int,
    remaining_actions: int | None,
) -> tuple[int, int, ActionResult | None, int, int]:
    """Read a channel and maybe react. Returns reads, reactions, flood, fails, attempts."""
    warm = settings.warming
    reads = reactions = failures = attempts = 0
    read_result = await _seams.execute(
        account_id,
        ReadChannel(channel=channel, message_limit=warm.read_message_limit),
    )
    attempts += 1
    if read_result.status == "ok":
        reads = 1
    elif read_result.status == "failed":
        failures += 1
    elif read_result.status in _HALT_STATUSES:
        return reads, reactions, read_result, failures, attempts
    await _human_pause(warm.reading_min_seconds, warm.reading_max_seconds)
    # Don't react to a channel whose read just failed: it's a pointless extra
    # request on a ban-risk account and yields a contradictory status=failed +
    # reactions_sent=1 result (#100).
    can_react = read_result.status == "ok"
    if remaining_actions is not None and (attempts_so_far + attempts) >= remaining_actions:
        can_react = False

    if can_react and reactions_enabled and _seams.rng.random() < reaction_probability:
        react_result = await _seams.execute(
            account_id,
            ReactToPost(
                channel=channel,
                reactions=warm.default_reactions,
                message_limit=warm.reaction_message_limit,
            ),
        )
        attempts += 1
        if react_result.status in _HALT_STATUSES:
            return reads, reactions, react_result, failures, attempts
        if react_result.status == "ok":
            reactions = 1
        elif react_result.status == "failed":
            failures += 1
    return reads, reactions, None, failures, attempts


@dataclass
class _ChannelTally:
    """Running totals + flood signals accumulated across a cycle's channels."""

    joined: int = 0
    reads: int = 0
    reactions: int = 0
    failures: int = 0
    attempts: int = 0
    flood_seconds: int | None = None
    flood_until: str | None = None
    last_failed_action: str | None = None
    last_failed_channel: str | None = None
    flooded: bool = False
    peer_flooded: bool = False


def _apply_join_result(tally: _ChannelTally, result: ActionResult, channel: str) -> bool:
    """Fold a join result into the tally. Returns True if the cycle should stop."""
    if result.status == "ok":
        tally.joined += 1
        return False
    tally.last_failed_action = "join"
    tally.last_failed_channel = channel
    if result.status == "failed":
        tally.failures += 1
        return False
    if result.status == "peer_flood":
        tally.peer_flooded = True
        return True
    if result.status in _WAIT_STATUSES:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(result)
        return True
    return False


def _apply_read_result(
    tally: _ChannelTally,
    outcome: tuple[int, int, ActionResult | None, int, int],
    channel: str,
) -> bool:
    """Fold a read/react outcome into the tally. Returns True if the cycle should stop."""
    reads, reactions, channel_flood, failures, attempts = outcome
    tally.reads += reads
    tally.reactions += reactions
    tally.failures += failures
    tally.attempts += attempts
    if failures:
        tally.last_failed_action = "read_or_react"
        tally.last_failed_channel = channel
    if channel_flood is None:
        return False
    if channel_flood.status == "peer_flood":
        tally.peer_flooded = True
    else:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(channel_flood)
    tally.last_failed_action = channel_flood.action_type
    tally.last_failed_channel = channel
    return True


async def _run_channel_loop(  # noqa: PLR0913, C901
    data: WarmingCycleRequest,
    chosen: list[WarmingChannel],
    secret: WarmingSettingsSecret,
    intensity: WarmingIntensity,
    attempts_so_far: int,
    on_step: _OnStep | None = None,
) -> _ChannelTally:
    warm = settings.warming
    account_id = data.account_id
    remaining_actions = data.remaining_actions
    tally = _ChannelTally()

    def _can_attempt() -> bool:
        if remaining_actions is None:
            return True
        return (attempts_so_far + tally.attempts) < remaining_actions

    for channel in chosen:
        if not _can_attempt():
            break
        if secret.join_enabled and not await is_channel_joined(account_id, channel.channel):
            join_result = await _seams.execute(account_id, JoinChannel(channel=channel.channel))
            tally.attempts += 1
            if join_result.status == "ok":
                await record_channel_joined(account_id, channel.channel)
                await _emit_step(on_step, "join")
            if _apply_join_result(tally, join_result, channel.channel):
                break
            await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
            if not _can_attempt():
                break
        outcome = await _read_and_react(
            account_id,
            channel.channel,
            reactions_enabled=secret.reactions_enabled,
            reaction_probability=intensity.reaction_probability,
            attempts_so_far=attempts_so_far + tally.attempts,
            remaining_actions=remaining_actions,
        )
        reads, reactions, *_ = outcome
        if reads:
            await _emit_step(on_step, "read")
        if reactions:
            await _emit_step(on_step, "react")
        if _apply_read_result(tally, outcome, channel.channel):
            break
        await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
    return tally


async def _set_offline(account_id: str) -> None:
    """SetOnline(False), swallowing errors — cleanup must never raise."""
    try:
        await _seams.execute(account_id, SetOnline(online=False))
    except Exception as exc:  # noqa: BLE001 - cleanup must never raise.
        await log_event(
            "WARNING",
            "warming_set_offline_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )


async def _build_cycle_result(
    account_id: str,
    tally: _ChannelTally,
    messages_sent: int,
) -> WarmingCycleResult:
    if tally.peer_flooded:
        status = "peer_flood"
    elif tally.flooded:
        status = "flood_wait"
    elif tally.failures:
        status = "failed"
    else:
        status = "ok"
    result = WarmingCycleResult(
        account_id=account_id,
        status=status,
        channels_joined=tally.joined,
        channels_read=tally.reads,
        reactions_sent=tally.reactions,
        messages_sent=messages_sent,
        flood_wait_seconds=tally.flood_seconds,
        flood_wait_until=tally.flood_until,
        failures=tally.failures,
        attempted_actions=tally.attempts,
        last_failed_action=tally.last_failed_action,
        last_failed_channel=tally.last_failed_channel,
    )
    await log_event(
        "WARNING" if status != "ok" else "INFO",
        "warming_cycle_completed",
        account_id=account_id,
        extra={
            "status": status,
            "joined": tally.joined,
            "reads": tally.reads,
            "reactions": tally.reactions,
            "messages": messages_sent,
            "failures": tally.failures,
            "flood_wait_seconds": tally.flood_seconds,
        },
    )
    return result


async def run_one_cycle(  # noqa: C901, PLR0912, PLR0915
    data: WarmingCycleRequest,
    *,
    on_step: _OnStep | None = None,
) -> WarmingCycleResult:
    """Perform exactly one warming pass for an account. The testable core.

    ``on_step`` (optional) is fired with the canonical step name after each
    successful action so the loop can persist live mid-cycle progress.
    """
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
    # ponytail: trust_band is intentionally omitted here — only phase/daily_cap
    # depend on it and those are enforced by the loop (remaining_actions), not
    # read in this cycle. Pass it in if channel/reaction/DM intensity ever
    # becomes trust-dependent (#100).
    intensity = compute_intensity(_account_age_hours(account, datetime.now(UTC)))
    tally = _ChannelTally()
    messages_sent = 0
    online_set = False

    def _can_attempt() -> bool:
        if data.remaining_actions is None:
            return True
        return tally.attempts < data.remaining_actions

    if not _can_attempt():
        return await _build_cycle_result(account_id, tally, messages_sent)

    try:
        online_result = await _seams.execute(account_id, SetOnline(online=True))
        tally.attempts += 1
        if online_result.status != "ok":
            if online_result.status in _WAIT_STATUSES:
                flooded, seconds, until = _classify_flood(online_result)
                tally.flooded = flooded
                tally.flood_seconds = seconds
                tally.flood_until = until
            elif online_result.status == "peer_flood":
                tally.peer_flooded = True
            else:
                tally.failures += 1
                tally.last_failed_action = "set_online"
            return await _build_cycle_result(account_id, tally, messages_sent)

        online_set = True
        await _emit_step(on_step, "set_online")
        await _human_pause(warm.typing_min_seconds, warm.typing_max_seconds)

        upper = min(intensity.channels_max, len(channels))
        lower = min(intensity.channels_min, upper)
        chosen = _seams.rng.sample(channels, _seams.rng.randint(lower, upper))
        channel_tally = await _run_channel_loop(
            data, chosen, secret, intensity, tally.attempts, on_step
        )

        tally.joined += channel_tally.joined
        tally.reads += channel_tally.reads
        tally.reactions += channel_tally.reactions
        tally.failures += channel_tally.failures
        tally.attempts += channel_tally.attempts
        tally.flood_seconds = channel_tally.flood_seconds
        tally.flood_until = channel_tally.flood_until
        tally.last_failed_action = channel_tally.last_failed_action
        tally.last_failed_channel = channel_tally.last_failed_channel
        tally.flooded = channel_tally.flooded
        tally.peer_flooded = channel_tally.peer_flooded

        if (
            _can_attempt()
            and not tally.flooded
            and not tally.peer_flooded
            and intensity.dm_allowed
            and secret.inter_account_chat
            and secret.gemini_api_key
        ):
            chat_result = await _maybe_inter_account_chat(account_id, secret)
            messages_sent = chat_result.messages_sent
            if messages_sent:
                await _emit_step(on_step, "send_dm")
            tally.attempts += chat_result.attempted_actions
            tally.failures += chat_result.failures
            if chat_result.last_failed_action:
                tally.last_failed_action = chat_result.last_failed_action
            if chat_result.flood_result:
                if chat_result.flood_result.status == "peer_flood":
                    tally.peer_flooded = True
                else:
                    tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(
                        chat_result.flood_result
                    )
                tally.last_failed_action = chat_result.last_failed_action or "send_dm"
    finally:
        # SetOnline(False) must run even if any of the inner steps raises so the
        # account does not stay online forever.
        if online_set:
            await _set_offline(account_id)

    return await _build_cycle_result(account_id, tally, messages_sent)
