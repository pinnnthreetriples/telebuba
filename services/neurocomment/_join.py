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
from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import (
    list_active_watch_channels,
    list_exhausted_watch_channels,
    list_joined_watch_channels,
    mark_watch_channel_join_lost,
    record_join,
)
from core.logging import log_event
from schemas.telegram_actions import JoinChannel
from services._join_lock import join_lock
from services.neurocomment import _seams
from services.neurocomment._generate import _COOLDOWN_STATUSES
from services.neurocomment.onboarding import _at_join_cap


async def run_join_pass(  # noqa: C901 - anti-ban decision ladder
    listener_account_id: str,
    *,
    generation: int | None = None,
) -> None:
    """One paced join pass over the *current* active watch set.

    Re-reads the watch set on every pass so a coalesced rerun picks up channels
    linked mid-pace. The listener only receives updates for channels it has joined,
    so a per-channel failure is logged (not fatal) and the burst breaks on the
    rolling-24h cap or a flood/cooldown status. Jittered pause runs *between* actual
    joins only (cache-hits skip it, none before the first) so a large watch set never
    fires as one join burst — the freeze vector.

    That cap is counted twice before a join goes out: once before the pause, and again under
    ``services._join_lock`` — the per-account mutex neuroshilling's ``join_target`` and
    neurocomment's pair onboarding take over this same log — which covers the join and
    the charge as well. The pause itself is left outside that mutex.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    channels = (await list_active_watch_channels()).channels
    nc = settings.neurocomment
    max_attempts = nc.listener_rejoin_max_attempts
    # One window value for both the charge and the verdict below, read once, so a pass can
    # never charge an attempt against a boundary the eligibility read has already moved past.
    since_iso = (
        datetime.now(UTC) - timedelta(hours=nc.listener_rejoin_attempt_window_hours)
    ).isoformat()
    # Before the seeding below, so a channel we were kicked from is no longer in either
    # cache by the time the loop reads them.
    await _mark_lost_channels(listener_account_id, max_attempts, since_iso)
    # Seed the cache from the join log: Telegram answers "ok" (never already_participant)
    # when a public channel is re-joined, so before this every restart re-sent the whole
    # watch set as real joins and pinned the account at its rolling-24h cap.
    _runtime._JOINED_CHANNELS.update(  # noqa: SLF001 - peer module
        (listener_account_id, channel)
        for channel in await list_joined_watch_channels(listener_account_id)
    )
    # Channels this account has already spent its whole re-join budget losing. Read from
    # the log, not from the in-memory cache, because the trigger for a fresh pass IS a
    # restart / Start / channel edit — a memory-only verdict would be forgiven by the very
    # events that re-run this, which is how the loop stayed unbounded.
    given_up = await list_exhausted_watch_channels(listener_account_id, max_attempts, since_iso)
    first_join = True
    for channel in channels:
        if generation is not None and not _runtime._runtime_owner_is_current(  # noqa: SLF001
            listener_account_id,
            generation,
        ):
            return
        if (listener_account_id, channel) in _runtime._JOINED_CHANNELS:  # noqa: SLF001 - peer module
            continue
        if channel in given_up:
            # Silently: the give-up was logged once, when the last attempt was spent, and
            # this pass runs on every reconcile — a line here would be the log flood the
            # cap warnings already were.
            continue
        # Rolling-24h join cap (anti-freeze): once the listener hits its cap, stop the
        # burst — remaining channels retry on the next reconcile as the window rolls.
        if await _at_join_cap(listener_account_id):
            await _log_daily_cap(listener_account_id, channel)
            break
        # Paced OUTSIDE the mutex below, and that placement is the point: this pause is
        # 30-120s by default, and holding the join mutex across it would stop a
        # neuroshilling campaign from joining anything on this same account for minutes.
        if not first_join:
            await asyncio.sleep(_runtime._join_jitter_seconds())  # noqa: SLF001 - peer module
            if generation is not None and not _runtime._runtime_owner_is_current(  # noqa: SLF001
                listener_account_id,
                generation,
            ):
                return
        first_join = False
        async with join_lock(listener_account_id):
            # Counted a second time, because the count above is spent by whoever charges
            # first and the pause that can sit between them is minutes long:
            # neuroshilling's ``join_target`` charges the same log for the same account,
            # and nothing refuses the listener account a campaign roster. Without this
            # count the mutex would only queue the joins that had already passed the
            # first one, and every one of them would still go out. Ends the burst the
            # same way and with the same event, so the refusals cannot be told apart.
            if await _at_join_cap(listener_account_id):
                await _log_daily_cap(listener_account_id, channel)
                break
            result = await _seams.execute(listener_account_id, JoinChannel(channel=channel))
            if result.status in {"ok", "already_participant"}:
                # Either way the account IS in the channel → cache it so we stop
                # re-joining. Only a real join counts against the rolling-24h cap; an
                # already-participant no-op (e.g. every channel on a restart) must not,
                # else the count pins near the cap and starves genuine joins.
                _runtime._JOINED_CHANNELS.add((listener_account_id, channel))  # noqa: SLF001
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


async def _log_daily_cap(listener_account_id: str, channel: str) -> None:
    """Log the join this pass did not send, because the rolling-24h budget is gone.

    One copy for the two counts that can refuse it — the one before the jittered pause
    and the one under the join mutex — so a refusal cannot reach the log worded two ways
    depending on which count caught it. The ``break`` stays at both call sites: the whole
    burst ends on either.
    """
    await log_event(
        "WARNING",
        "neurocomment_join_daily_cap",
        account_id=listener_account_id,
        extra={"channel": channel},
    )


async def _mark_lost_channels(
    listener_account_id: str,
    max_attempts: int,
    since_iso: str,
) -> None:
    """Disprove the standing joins of channels Telegram proved the listener is out of.

    Both caches say "joined" forever otherwise: the in-memory pair set for the life of
    the process, and the join log across restarts (#40 made it restart-safe on purpose).
    A kick therefore silenced the channel permanently — the listener only receives
    updates for channels it is in, the pass skipped it on the cache hit, and nothing
    ever re-joined it. ``_rejoin`` does not cover this: it recovers the comment account's
    access to a channel's *discussion group*, a different pair and a different table.

    Bounded by attempts, not by the rolling-24h join cap: that cap counts joins per
    ACCOUNT, and a looping channel spends exactly one join per pass while its stamped row
    stays counted, so the cap can never accumulate against it. The attempt budget is the
    brake (``listener_rejoin_max_attempts``), and it is spent on the LOSS rather than on the
    poke, so a channel whose peer never resolves converges on "leave it alone" instead of
    looping. Only losses inside ``since_iso`` count, so that verdict expires by itself
    rather than silencing a channel for ever the way an all-time count did.

    The in-memory eviction is deliberately tied to the stamp landing: with no standing join
    to disprove there is also nothing to count, so re-opening the pair would be a re-join
    nothing could ever bound.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    for channel in _runtime.take_lost_access_channels(listener_account_id):
        attempts = await mark_watch_channel_join_lost(listener_account_id, channel, since_iso)
        if attempts is None:
            # Nothing to charge, so nothing to evict either (see above) — and if the pair is
            # ALSO cached as joined, the loop below skips it and the channel goes unwatched
            # until a restart. Reachable via ``already_participant`` (caches the pair, records
            # no row) and after a retention purge takes a standing row. Gated on the cache
            # because the other two ways here are harmless: an uncached channel is re-joined
            # by the loop anyway, and a given-up one was evicted when its last row was
            # stamped, so it would otherwise print on every reconcile for ever.
            if (listener_account_id, channel) in _runtime._JOINED_CHANNELS:  # noqa: SLF001 - peer module
                await log_event(
                    "WARNING",
                    "neurocomment_listener_access_lost_untracked",
                    account_id=listener_account_id,
                    extra={"channel": channel},
                )
            continue
        _runtime._JOINED_CHANNELS.discard((listener_account_id, channel))  # noqa: SLF001 - peer module
        if attempts >= max_attempts:
            await log_event(
                "WARNING",
                "neurocomment_listener_rejoin_exhausted",
                account_id=listener_account_id,
                # The budget, not ``attempts``: this branch is "at or over it", and a
                # window that rolled under a long-lost channel can hand back more losses
                # than the budget has room for — "3/2" would read as a bug where "2/2"
                # closes the run of positions the access-lost lines printed.
                extra={
                    "channel": channel,
                    "attempts": attempts,
                    "reason": f"{max_attempts}/{max_attempts}",
                },
            )
            continue
        await log_event(
            "WARNING",
            "neurocomment_listener_access_lost",
            account_id=listener_account_id,
            # Which re-join this loss buys, out of how many: ``attempts`` alone said
            # nothing about the budget it was spending, so a channel on its last try read
            # exactly like one on its first. Rendered raw beside the label by
            # ``eventReason`` — no translation, no new event code.
            extra={
                "channel": channel,
                "attempts": attempts,
                "reason": f"{attempts}/{max_attempts}",
            },
        )
