"""Paced listener channel-join pass — split out of ``services.neurocomment._runtime``.

The listener account is joined to each watched channel with a jittered pause
(anti-freeze); running that inline blocked Start (under the per-account lock) and
channel-edit requests for minutes, so it now runs as a single-flighted background
task. The task HANDLE + rerun flag stay in ``_runtime`` (tests rebind those module
globals directly, and re-exported names don't track reassignment); this module
holds only the loop *body*, which calls back into ``_runtime`` for the jitter and
the join cache so tests that monkeypatch ``_runtime._join_jitter_seconds`` /
``_runtime._JOINED_CHANNELS`` still see the patch.
"""

from __future__ import annotations

import asyncio

from core.db import (
    forget_watch_channel_join,
    list_active_watch_channels,
    list_joined_watch_channels,
    record_join,
)
from core.logging import log_event
from schemas.telegram_actions import JoinChannel
from services.neurocomment import _seams
from services.neurocomment._generate import _COOLDOWN_STATUSES
from services.neurocomment.onboarding import _at_join_cap


async def run_join_pass(listener_account_id: str) -> None:
    """One paced join pass over the *current* active watch set.

    Re-reads the watch set on every pass so a coalesced rerun picks up channels
    linked mid-pace. The listener only receives updates for channels it has joined,
    so a per-channel failure is logged (not fatal) and the burst breaks on the
    rolling-24h cap or a flood/cooldown status. Jittered pause runs *between* actual
    joins only (cache-hits skip it, none before the first) so a large watch set never
    fires as one join burst — the freeze vector.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    channels = (await list_active_watch_channels()).channels
    # Before the seeding below, so a channel we were kicked from is no longer in either
    # cache by the time the loop reads them.
    await _forget_lost_channels(listener_account_id)
    # Seed the cache from the join log: Telegram answers "ok" (never already_participant)
    # when a public channel is re-joined, so before this every restart re-sent the whole
    # watch set as real joins and pinned the account at its rolling-24h cap.
    _runtime._JOINED_CHANNELS.update(  # noqa: SLF001 - peer module
        (listener_account_id, channel)
        for channel in await list_joined_watch_channels(listener_account_id)
    )
    first_join = True
    for channel in channels:
        if (listener_account_id, channel) in _runtime._JOINED_CHANNELS:  # noqa: SLF001 - peer module
            continue
        # Rolling-24h join cap (anti-freeze): once the listener hits its cap, stop the
        # burst — remaining channels retry on the next reconcile as the window rolls.
        if await _at_join_cap(listener_account_id):
            await log_event(
                "WARNING",
                "neurocomment_join_daily_cap",
                account_id=listener_account_id,
                extra={"channel": channel},
            )
            break
        if not first_join:
            await asyncio.sleep(_runtime._join_jitter_seconds())  # noqa: SLF001 - peer module
        first_join = False
        result = await _seams.execute(listener_account_id, JoinChannel(channel=channel))
        if result.status in {"ok", "already_participant"}:
            # Either way the account IS in the channel → cache it so we stop re-joining.
            # Only a real join counts against the rolling-24h cap; an already-participant
            # no-op (e.g. every channel on a restart) must not, else the count pins near
            # the cap and starves genuine joins.
            _runtime._JOINED_CHANNELS.add((listener_account_id, channel))  # noqa: SLF001 - peer module
            if result.status == "ok":
                await record_join(listener_account_id, watch_channel=channel)
            continue
        # ``error_type`` (the Telegram exception class) is what turns a bare status="failed"
        # into an actionable line; absent rather than null when the gateway set none, which
        # is always the case for the flood family below.
        extra: dict[str, object] = {"channel": channel, "status": result.status}
        if result.error_type:
            extra["error_type"] = result.error_type
        if result.status in _COOLDOWN_STATUSES:
            # Telegram is rate-limiting this account: stop the burst rather than fire the
            # next RPC and escalate a soft flood-wait into a hard freeze. Unjoined channels
            # retry on the next reconcile (only ok joins are cached).
            await log_event(
                "WARNING",
                "neurocomment_listener_join_flood",
                account_id=listener_account_id,
                extra=extra,
            )
            break
        await log_event(
            "WARNING",
            "neurocomment_listener_join_failed",
            account_id=listener_account_id,
            extra=extra,
        )


async def _forget_lost_channels(listener_account_id: str) -> None:
    """Undo the join bookkeeping for channels Telegram proved the listener is out of.

    Both caches say "joined" forever otherwise: the in-memory pair set for the life of
    the process, and the join log across restarts (#40 made it restart-safe on purpose).
    A kick therefore silenced the channel permanently — the listener only receives
    updates for channels it is in, the pass skipped it on the cache hit, and nothing
    ever re-joined it. ``_rejoin`` does not cover this: it recovers the comment account's
    access to a channel's *discussion group*, a different pair and a different table.

    Only proven losses arrive here (``core.telegram_client._listener._LOST_ACCESS_ERRORS``),
    never a transient resolution failure, so this cannot become the re-join storm the
    join-log cache was introduced to stop — and the rolling-24h cap still bounds whatever
    the next pass does.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    for channel in _runtime.take_lost_access_channels(listener_account_id):
        _runtime._JOINED_CHANNELS.discard((listener_account_id, channel))  # noqa: SLF001 - peer module
        await forget_watch_channel_join(listener_account_id, channel)
        await log_event(
            "WARNING",
            "neurocomment_listener_access_lost",
            account_id=listener_account_id,
            extra={"channel": channel},
        )
