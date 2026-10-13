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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from core.config import settings
from core.db import (
    fetch_account,
    list_joined_channels,
    list_warming_channels,
    load_warming_settings,
)
from core.logging import log_event
from schemas._warming_visits import VisitPlan
from schemas.telegram_actions import SetOnline
from schemas.warming import WarmingCycleRequest, WarmingCycleResult
from services.warming import _seams
from services.warming._channel_language import account_language
from services.warming._chat import _run_chat_step
from services.warming._chat_live import remember_read_posts
from services.warming._extras import run_extras_step
from services.warming._extras_ctx import _ExtraContext
from services.warming._fleet import (
    _account_channel_affinity,
    _affinity_epoch,
    _language_pool,
    _maybe_explore,
)
from services.warming._snapshot import current_snapshot
from services.warming._social_reads import walkable_channels
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
)

if TYPE_CHECKING:
    from schemas._warming_extras import JoinedChannel
    from schemas.warming import WarmingChannel, WarmingIntensity
    from services.warming._steps import _OnStep

# The visits that walk the whole classic route: stories, DM and extras.
_FULL_VISITS = frozenset({"normal", "binge"})

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


def _without_cooling_down(
    chosen: list[WarmingChannel], joined: list[JoinedChannel], now: datetime
) -> list[WarmingChannel]:
    """Drop channels the account left less than the re-join cooldown ago.

    A left channel is neither read nor re-joined until then; once the cooldown lapses
    ``is_channel_joined`` is False (``left_at`` set), so the join step re-joins it and
    its upsert clears ``left_at``.
    """
    cutoff = now - timedelta(days=settings.warming.extras_rejoin_cooldown_days)
    cooling = {
        j.channel
        for j in joined
        if j.left_at is not None and datetime.fromisoformat(j.left_at) > cutoff
    }
    return [c for c in chosen if c.channel not in cooling]


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


async def _extras_step(ctx: _ExtraContext, on_step: _OnStep | None) -> None:
    """Run the drawn extras and advance the rail only if at least one landed."""
    if await run_extras_step(ctx):
        await _emit_step(on_step, "extras")


async def _before_channels(ctx: _ExtraContext, visit: VisitPlan, on_step: _OnStep | None) -> None:
    """What the visit's order puts before the channels: one free read extra, the stories.

    Both are reads, so a visit never writes before it has looked at its channels.
    """
    if visit.read_extra_first and await run_extras_step(ctx, before_channels=True):
        await _emit_step(on_step, "extras")
    if visit.stories_first and visit.kind in _FULL_VISITS:
        await _watch_stories_step(
            ctx.account_id, ctx.chosen, ctx.tally, on_step, can_attempt=ctx.can_attempt()
        )


async def _set_offline(account_id: str) -> None:
    """SetOnline(False), swallowing errors — cleanup must never raise.

    Runs inside ``cleanup_scope`` because Stop revokes the generation's lease
    *before* cancelling it, so by the time this ``finally`` runs the fence would
    refuse the very RPC that hands the account back — and ``except Exception``
    below would swallow the refusal, leaving the account online after every Stop.
    """
    try:
        with _seams.cleanup_scope():
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
            "extras": tally.extras,
            "failures": tally.failures,
            "flood_wait_seconds": tally.flood_seconds,
        },
    )
    return result


def _pick_channels(
    account_id: str,
    channels: list[WarmingChannel],
    language: str | None,
    intensity: WarmingIntensity,
    count: int | None = None,
) -> list[WarmingChannel]:
    """This session's channels: a draw from the account's own-language interest set.

    Exploration stays inside the same language pool, so a stray pick never puts a
    Mexican number on a Russian feed the pool had no reason to include. ``count``
    is the visit's own number (a glance reads one, a binge more), drawn as is.
    """
    affinity = _account_channel_affinity(
        account_id, channels, _affinity_epoch(datetime.now(UTC)), language
    )
    if count is not None:
        return _seams.rng.sample(affinity, min(count, len(affinity)))
    upper = min(intensity.channels_max, len(affinity))
    lower = min(intensity.channels_min, upper)
    chosen = _seams.rng.sample(affinity, _seams.rng.randint(lower, upper))
    pool, _tiers = _language_pool(channels, language)
    return _maybe_explore(chosen, pool, affinity, account_id, _seams.rng)


async def _go_online(account_id: str, tally: _ChannelTally) -> bool:
    """SetOnline(True), folding a refusal into ``tally``. True when the account is online."""
    # Book before dispatch: cancellation can land while the RPC is already
    # outside the process, so its outcome is ambiguous and must count spent.
    tally.attempts += 1
    online_result = await _seams.execute(account_id, SetOnline(online=True))
    if online_result.status == "ok":
        return True
    if online_result.status in _WAIT_STATUSES:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(online_result)
    elif online_result.status == "peer_flood":
        tally.peer_flooded = True
    else:
        tally.failures += 1
        tally.last_failed_action = "set_online"
    return False


async def _after_channels(
    ctx: _ExtraContext,
    data: WarmingCycleRequest,
    visit: VisitPlan,
    on_step: _OnStep | None,
    *,
    dm_allowed: bool,
) -> int:
    """Stories, the DM turn and the extras, as the visit's kind allows; returns DMs sent.

    A glance stops after its channel; «Только ответить» only answers (the persona's DM
    roll is skipped, every DM gate stays); normal and binge visits walk the whole route.
    """
    # One low-risk "glanced at stories" signal per session (every persona).
    if visit.kind in _FULL_VISITS and not visit.stories_first:
        await _watch_stories_step(
            ctx.account_id, ctx.chosen, ctx.tally, on_step, can_attempt=ctx.can_attempt()
        )
    messages_sent = 0
    if visit.kind != "glance":
        remember_read_posts(ctx.recent_ids)
        messages_sent = await _run_chat_step(
            data,
            ctx.secret,
            ctx.tally,
            dm_allowed=dm_allowed,
            can_attempt=ctx.can_attempt(),
            forced=visit.kind == "reply",
        )
        if messages_sent:
            await _emit_step(on_step, "send_dm")
    # Extras go last so the budget-booking steps above never starve behind them.
    if visit.kind in _FULL_VISITS:
        await _extras_step(ctx, on_step)
    return messages_sent


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
    # Inside an iteration the gates already read all of it (5.1); a direct call reads.
    snapshot = current_snapshot(account_id)
    secret = snapshot.controls if snapshot else await load_warming_settings()
    account = snapshot.account if snapshot else await fetch_account(account_id)
    # ponytail: trust_band is intentionally omitted here — only phase/daily_cap
    # depend on it and those are enforced by the loop (remaining_actions), not
    # read in this cycle. Pass it in if channel/reaction/DM intensity ever
    # becomes trust-dependent (#100).
    intensity = compute_intensity(_account_age_hours(account, datetime.now(UTC)))
    # Approved rows only, and public groups only from their phase on (4.5/4.6).
    approved = list(snapshot.channels) if snapshot else (await list_warming_channels()).channels
    channels = walkable_channels(approved, intensity.phase)
    if not channels:
        await log_event("WARNING", "warming_no_channels", account_id=account_id)
        return WarmingCycleResult(
            account_id=account_id,
            status="skipped",
            detail="no channels configured",
        )

    warm = settings.warming
    tally = tally if tally is not None else _ChannelTally()
    messages_sent = 0
    online_set = False
    if data.remaining_actions is not None and tally.attempts >= data.remaining_actions:
        return await _build_cycle_result(account_id, tally, messages_sent)

    try:
        online_set = await _go_online(account_id, tally)
        if not online_set:
            return await _build_cycle_result(account_id, tally, messages_sent)
        await _emit_step(on_step, "set_online")
        await _human_pause(warm.typing_min_seconds, warm.typing_max_seconds)

        # The visit's kind and order (4.4); a direct call runs a normal visit.
        visit = data.visit or VisitPlan()
        language = account_language(account.phone if account else None)
        chosen = _pick_channels(account_id, channels, language, intensity, visit.channels)
        joined = list(snapshot.joined) if snapshot else await list_joined_channels(account_id)
        chosen = _without_cooling_down(chosen, joined, datetime.now(UTC))
        ctx = _ExtraContext(
            account_id=account_id,
            secret=secret,
            persona=data.activity_persona,
            chosen=chosen,
            recent_ids={},
            tally=tally,
            remaining_actions=data.remaining_actions,
            joined=joined,
            account=account,
            media_bytes_left=warm.extras_media_bytes_per_cycle,
        )
        # Small things before the channels — only ever reads, never a write.
        await _before_channels(ctx, visit, on_step)
        ctx.recent_ids, ctx.recent_media = await _run_channel_loop(
            data,
            tally,
            chosen,
            secret,
            on_step,
            phase=intensity.phase,
        )
        if visit.linger_seconds > 0:
            await _seams.sleep(visit.linger_seconds)
        dm_ok = data.dm_allowed if data.dm_allowed is not None else intensity.dm_allowed
        messages_sent = await _after_channels(ctx, data, visit, on_step, dm_allowed=dm_ok)
    finally:
        # SetOnline(False) must run even if any of the inner steps raises so the
        # account does not stay online forever.
        if online_set:
            await _set_offline(account_id)

    return await _build_cycle_result(account_id, tally, messages_sent)
