"""The per-channel action steps of a warming cycle — join, read, react.

Split out of :mod:`services.warming._cycle` to keep that module under the
file-size budget. This half owns the channel walk (``_run_channel_loop``), the
read-then-maybe-react step, the tallies their outcomes fold into, and the pacing
/ progress primitives every step shares (``_human_delay`` / ``_human_pause`` /
``_emit_step``). ``_cycle`` keeps the session-level orchestration and imports
from here, never the other way round, so the dependency stays one-directional.
Telegram and randomness are still reached via :mod:`services.warming._seams` so
tests patch them in one place.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import settings
from core.db import is_channel_joined, record_channel_joined
from core.logging import log_event
from schemas.telegram_actions import JoinChannel, ReactToPost, ReadChannel
from services.warming import _seams
from services.warming.pacing import (
    _FAILURE_STATUSES,
    _HALT_STATUSES,
    _WAIT_STATUSES,
    _classify_flood,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.telegram_actions import ActionResult
    from schemas.warming import WarmingChannel, WarmingCycleRequest, WarmingSettingsSecret

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


@dataclass
class _ReadReactOutcome:
    """One channel's read-then-maybe-react result (replaces a positional 5-tuple)."""

    reads: int = 0
    reactions: int = 0
    flood: ActionResult | None = None
    failures: int = 0


async def _read_and_react(  # noqa: PLR0913
    account_id: str,
    channel: str,
    tally: _ChannelTally,
    *,
    reactions_enabled: bool,
    reaction_probability: float,
    remaining_actions: int | None,
) -> _ReadReactOutcome:
    """Read a channel and maybe react, tallying reads / reactions / fails / flood.

    ``attempts`` is counted on the caller's ``tally`` the instant each request
    returns — NOT folded in with the rest of the outcome after this returns. The
    reading pause between the two RPCs is the longest await in a cycle, so a
    cancellation there would otherwise hand the loop's reconcile a count that is
    short by everything this channel already spent (#208).
    """
    warm = settings.warming
    out = _ReadReactOutcome()
    # Read the larger reaction pool in one pass so the react reuses these ids.
    # Pre-book immediately before each RPC. If cancellation lands after dispatch,
    # the unknown Telegram outcome stays counted instead of being handed back.
    tally.attempts += 1
    read_result = await _seams.execute(
        account_id,
        ReadChannel(channel=channel, message_limit=warm.reaction_message_limit),
    )
    if read_result.status == "ok":
        out.reads = 1
    elif read_result.status in _FAILURE_STATUSES:
        out.failures += 1
    elif read_result.status in _HALT_STATUSES:
        out.flood = read_result
        return out
    await _human_pause(warm.reading_min_seconds, warm.reading_max_seconds)
    # Don't react to a channel whose read just failed: it's a pointless extra
    # request on a ban-risk account and yields a contradictory status=failed +
    # reactions_sent=1 result (#100).
    can_react = read_result.status == "ok"
    if remaining_actions is not None and tally.attempts >= remaining_actions:
        can_react = False

    if can_react and reactions_enabled and _seams.rng.random() < reaction_probability:
        tally.attempts += 1
        react_result = await _seams.execute(
            account_id,
            ReactToPost(
                channel=channel,
                reactions=warm.default_reactions,
                message_limit=warm.reaction_message_limit,
                message_ids=[int(x) for x in read_result.recent_message_ids or []] or None,
            ),
        )
        if react_result.status in _HALT_STATUSES:
            out.flood = react_result
            return out
        # A skipped react (channel permits no usable emoji) is status "ok" with no
        # message_id — don't count it as a reaction the board never actually placed.
        if react_result.status == "ok" and react_result.message_id is not None:
            out.reactions = 1
        elif react_result.status in _FAILURE_STATUSES:
            out.failures += 1
    elif can_react and reactions_enabled:
        # We could have reacted, but the persona's reaction dice missed this
        # cycle. Log it so the activity feed shows the decision (a human doesn't
        # react to every post) rather than silent inaction the operator can't see.
        await log_event(
            "INFO",
            "warming_reaction_skipped",
            account_id=account_id,
            extra={"channel": channel, "reason": "chance"},
        )
    return out


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
    if result.status in {"ok", "already_participant"}:
        # Already a member counts as a joined channel — warming has no rolling-24h
        # cap, so there is nothing to skip; it is success just like a real join.
        tally.joined += 1
        return False
    tally.last_failed_action = "join"
    tally.last_failed_channel = channel
    if result.status in _FAILURE_STATUSES:
        tally.failures += 1
        return False
    if result.status == "peer_flood":
        tally.peer_flooded = True
        return True
    if result.status in _WAIT_STATUSES:
        tally.flooded, tally.flood_seconds, tally.flood_until = _classify_flood(result)
        return True
    return False


def _apply_read_result(tally: _ChannelTally, outcome: _ReadReactOutcome, channel: str) -> bool:
    """Fold a read/react outcome into the tally. Returns True if the cycle should stop.

    ``attempts`` is deliberately absent: ``_read_and_react`` already counted each
    request on this same tally as it was spent, so adding it here again would
    double-bill every read and reaction (#208).
    """
    tally.reads += outcome.reads
    tally.reactions += outcome.reactions
    tally.failures += outcome.failures
    if outcome.failures:
        tally.last_failed_action = "read_or_react"
        tally.last_failed_channel = channel
    channel_flood = outcome.flood
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
    tally: _ChannelTally,
    chosen: list[WarmingChannel],
    secret: WarmingSettingsSecret,
    reaction_probability: float,
    on_step: _OnStep | None = None,
) -> None:
    """Walk the chosen channels, folding every outcome into the caller's ``tally``.

    The tally belongs to the caller (rather than being built here and merged on
    return) so the running ``attempts`` count is readable while the walk is still
    in flight: the loop reconciles its daily-budget reservation from it when a
    cycle is cancelled or raises mid-channel (#208).
    """
    warm = settings.warming
    account_id = data.account_id
    remaining_actions = data.remaining_actions

    def _can_attempt() -> bool:
        if remaining_actions is None:
            return True
        return tally.attempts < remaining_actions

    for channel in chosen:
        if not _can_attempt():
            break
        if secret.join_enabled and not await is_channel_joined(account_id, channel.channel):
            tally.attempts += 1
            join_result = await _seams.execute(account_id, JoinChannel(channel=channel.channel))
            if join_result.status in {"ok", "already_participant"}:
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
            tally,
            reactions_enabled=secret.reactions_enabled,
            reaction_probability=reaction_probability,
            remaining_actions=remaining_actions,
        )
        if outcome.reads:
            await _emit_step(on_step, "read")
        if outcome.reactions:
            await _emit_step(on_step, "react")
        if _apply_read_result(tally, outcome, channel.channel):
            break
        await _human_pause(warm.action_delay_min_seconds, warm.action_delay_max_seconds)
