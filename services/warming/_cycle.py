"""The warming cycle — one human-like pass of reads / reactions / joins / chat.

``run_one_cycle`` is the testable core. Telegram and randomness are reached via
:mod:`services.warming._seams` so tests patch them in one place.

Session orchestration only: pick the channels, walk them, glance at stories, chat,
go offline, build the result. The per-channel action steps and their tallies live
in :mod:`services.warming._steps`, split off to keep this module under the
file-size budget; the dependency runs one way (this module imports from
``_steps``, never the reverse).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_account, list_warming_channels, load_warming_settings
from core.logging import log_event
from schemas.telegram_actions import SetOnline
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.warming import _seams
from services.warming._chat import _run_chat_step
from services.warming._fleet import _account_channel_affinity, _affinity_epoch, _maybe_explore
from services.warming._steps import (
    _ChannelTally,
    _emit_step,
    _human_pause,
    _run_channel_loop,
)
from services.warming._stories import maybe_watch_stories
from services.warming.pacing import (
    _WAIT_STATUSES,
    _account_age_hours,
    _classify_flood,
    compute_intensity,
    persona_reaction_probability,
)

if TYPE_CHECKING:
    from schemas.warming import WarmingChannel
    from services.warming._steps import _OnStep

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


async def _watch_stories_step(
    account_id: str,
    chosen: list[WarmingChannel],
    tally: _ChannelTally,
    on_step: _OnStep | None,
    *,
    can_attempt: bool,
) -> None:
    """Glance at a peer's stories and advance the rail only if the view landed."""
    if await maybe_watch_stories(account_id, chosen, tally, can_attempt=can_attempt):
        await _emit_step(on_step, "stories")


async def _set_offline(account_id: str) -> None:
    """SetOnline(False), swallowing errors — cleanup must never raise."""
    try:
        await _seams.execute(account_id, SetOnline(online=False))
    except Exception as exc:  # cleanup must never raise.
        logger.exception("set_offline failed for %s", account_id)
        await log_event(
            "WARNING",
            "warming_set_offline_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
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


async def run_one_cycle(
    data: WarmingCycleRequest,
    *,
    on_step: _OnStep | None = None,
    tally: _ChannelTally | None = None,
) -> WarmingCycleResult:
    """Perform exactly one warming pass for an account. The testable core.

    ``on_step`` (optional) is fired with the canonical step name after each
    successful action so the loop can persist live mid-cycle progress.

    ``tally`` (optional) lets the caller own the running counters instead of this
    function allocating them, so a cycle that is cancelled or raises mid-flight
    still leaves behind the attempts it really spent — the loop reconciles its
    daily-budget reservation from that (#208).
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
    tally = tally if tally is not None else _ChannelTally()
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

        affinity = _account_channel_affinity(
            account_id, channels, _affinity_epoch(datetime.now(UTC))
        )
        upper = min(intensity.channels_max, len(affinity))
        lower = min(intensity.channels_min, upper)
        chosen = _seams.rng.sample(affinity, _seams.rng.randint(lower, upper))
        chosen = _maybe_explore(chosen, channels, affinity, account_id, _seams.rng)
        await _run_channel_loop(
            data,
            tally,
            chosen,
            secret,
            persona_reaction_probability(data.activity_persona),
            on_step,
        )

        # One low-risk "glanced at stories" signal per session (every persona).
        await _watch_stories_step(account_id, chosen, tally, on_step, can_attempt=_can_attempt())

        dm_ok = data.dm_allowed if data.dm_allowed is not None else intensity.dm_allowed
        messages_sent = await _run_chat_step(
            data, secret, tally, dm_allowed=dm_ok, can_attempt=_can_attempt()
        )
        if messages_sent:
            await _emit_step(on_step, "send_dm")
    finally:
        # SetOnline(False) must run even if any of the inner steps raises so the
        # account does not stay online forever.
        if online_set:
            await _set_offline(account_id)

    return await _build_cycle_result(account_id, tally, messages_sent)
