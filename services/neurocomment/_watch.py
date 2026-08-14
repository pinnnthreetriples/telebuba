"""Watch-set reconcile + the "this channel is dead to the engine" report.

Split out of :mod:`services.neurocomment._runtime` (file-size cap). Reconcile and
the unwatched-channel report belong in one module because the report is only ever
correct when it is published in the same await-free step as the subscription it
describes — see :func:`_publish_unwatched`.

The task handles and the ``_UNWATCHED_CHANNELS`` set itself stay in ``_runtime``
(tests rebind those module globals, and a re-exported name does not track
reassignment), so this module reaches back through the module object. That also
keeps the ``_runtime.subscribe_posts`` / ``stop_post_listener`` /
``list_warming_account_ids`` / ``log_event`` monkeypatch seams working.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.db import list_active_watch_channels

if TYPE_CHECKING:
    from collections.abc import Iterable

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)


def _publish_unwatched(channels: Iterable[str] = ()) -> None:
    """Replace the published gap set in one await-free step; no reader sees it torn.

    Clearing on reconcile entry and refilling only after ``subscribe_posts`` left a
    window — one serial ``get_peer_id`` RPC per uncached channel wide, and the channels
    that fail to resolve are exactly the slow ones — in which a runtime-status poll read
    an empty set and reported every channel as watched. The SPA polls that endpoint and
    reconcile fires on every channel link/unlink, so the danger strip blinked off on each
    edit. Union-updating was the mirror bug: the set became the union of every pass since
    the last clear while the live filter is whatever the LAST pass registered, so a
    resolved channel stayed flagged red until some later reconcile.

    ``clear()`` + ``update()`` with no await between them is atomic on the single event
    loop: readers only ever see a whole set, and the last pass to publish wins.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    _runtime._UNWATCHED_CHANNELS.clear()  # noqa: SLF001 - peer module
    _runtime._UNWATCHED_CHANNELS.update(channels)  # noqa: SLF001 - peer module


async def reconcile_neurocomment_runtime(listener_account_id: str) -> None:
    """(Re)point the listener at the current active watch set. Idempotent, returns promptly.

    No active channels → stop the listener (idempotent). Safe to call on every
    boot; ``subscribe_posts`` itself drops any prior handler before registering.
    Subscribing before the paced joins land is fine: Telethon only delivers updates
    for channels the account has actually joined, and ``subscribe_posts`` is
    idempotent, so the single-flighted background join task making channels live as
    it paces is acceptable — and keeps this call off the multi-minute join path.
    Channels still unresolved once that task drains are re-subscribed by its tail
    (:func:`_resubscribe_unwatched`), the only path that heals them in-process.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    async with _runtime.neurocomment_lifecycle():
        # The caller's id is only a hint. Re-read both persisted ownership fields while
        # holding the same lifecycle lock as start/stop/clear: a queued reconcile for A
        # must not replace B's subscription after a switch completes.
        current = await _runtime.get_listener_account_id()
        running = await _runtime.get_listener_running()
        if _runtime._RUNTIME_OWNER_INITIALIZED:  # noqa: SLF001
            if listener_account_id != _runtime._RUNTIME_ACCOUNT_ID:  # noqa: SLF001
                return
            if (current is not None or running) and (not running or current != listener_account_id):
                return
        generation = _runtime._activate_runtime_owner(listener_account_id)  # noqa: SLF001
        reconcile_generation = _runtime._reserve_reconcile()  # noqa: SLF001
    await _reconcile_owned(listener_account_id, generation, reconcile_generation)


async def _reconcile_owned(  # noqa: C901, PLR0911 - staged generation-fenced commit
    listener_account_id: str,
    generation: int,
    reconcile_generation: int,
) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    # Warming and neurocomment are mutually exclusive per account. This is the
    # single choke point every subscription path funnels through (start, channel
    # edit, startup resume), so the guard lives here — start_neurocomment adds an
    # early raise on top for the interactive 409. A warming listener is unsubscribed
    # (never re-subscribed) rather than raising, so boot/channel-edit stay safe.
    if listener_account_id in await _runtime.list_warming_account_ids():
        if not _runtime._reconcile_owner_is_current(  # noqa: SLF001
            listener_account_id, generation, reconcile_generation
        ):
            return
        async with _runtime.neurocomment_lifecycle():
            if not _runtime._reconcile_owner_is_current(  # noqa: SLF001
                listener_account_id, generation, reconcile_generation
            ):
                return
            await _runtime.stop_post_listener(listener_account_id)
            await _runtime._inbox_runtime.stop_inbox()  # noqa: SLF001 - peer module
            await _runtime._stop_sweep()  # noqa: SLF001 - peer module
            await _runtime._stop_join()  # noqa: SLF001 - peer module
            _publish_unwatched((await list_active_watch_channels()).channels)
            await _runtime.log_event(
                "WARNING",
                "neurocomment_listener_warming_skipped",
                account_id=listener_account_id,
            )
        return
    channels = (await list_active_watch_channels()).channels
    if not _runtime._reconcile_owner_is_current(  # noqa: SLF001
        listener_account_id, generation, reconcile_generation
    ):
        return
    if not channels:
        async with _runtime.neurocomment_lifecycle():
            if not _runtime._reconcile_owner_is_current(  # noqa: SLF001
                listener_account_id, generation, reconcile_generation
            ):
                return
            await _runtime.stop_post_listener(listener_account_id)
            await _runtime._inbox_runtime.stop_inbox()  # noqa: SLF001 - peer module
            await _runtime._stop_sweep()  # noqa: SLF001 - peer module
            await _runtime._stop_join()  # noqa: SLF001 - peer module
            _publish_unwatched()  # unsubscribed → no channel is "requested but missing"
            # Same code and fields as the tail below, because this IS a finished reconcile — it
            # resolved to "watch nothing". It returned before either log line, so the listener
            # went silent with ``listener_running`` still set and not a word about it. Deleting
            # the last campaign is precisely how an operator reaches here, and that delete is
            # now recorded, so its consequence must not be the missing half. Inside the
            # lifecycle lock with the teardown it reports, so the two cannot be observed apart.
            await _runtime.log_event(
                "INFO",
                "neurocomment_runtime_reconciled",
                account_id=listener_account_id,
                extra={"channels": 0, "unwatched": 0},
            )
        return
    plans = await _runtime._inbox_runtime.prepare_backfill_plans(channels)  # noqa: SLF001
    if not _runtime._reconcile_owner_is_current(  # noqa: SLF001
        listener_account_id, generation, reconcile_generation
    ):
        return
    subscribed = await _runtime.subscribe_posts(listener_account_id, channels, _runtime.on_post)
    async with _runtime.neurocomment_lifecycle():
        # A Stop/clear/account switch is allowed to invalidate us while Telegram peer
        # resolution is in flight.  The core listener normally observes its own bumped
        # subscription generation and refuses the late commit, but keep this service
        # boundary independently safe too: alternate gateways/test seams may complete a
        # subscribe after invalidation.  Re-check while holding the lifecycle lock and
        # tear that stale account down before a same-account restart can publish a new
        # owner.  A merely superseded reconcile for the *current* owner must not stop the
        # newer pass, so cleanup is keyed to runtime ownership, not reconcile generation.
        if not _runtime._runtime_owner_is_current(  # noqa: SLF001
            listener_account_id, generation
        ):
            # Stop/clear/account switch leaves this account with no owner, so an
            # alternate gateway that committed late must be cleaned up. A same-account
            # restart, however, may already have published a NEW subscription while this
            # old pass was outside the lock; account-wide stop would remove that winner.
            # The real core gateway generation-fences the old commit itself, so leave the
            # new same-account owner untouched.
            if listener_account_id != _runtime._RUNTIME_ACCOUNT_ID:  # noqa: SLF001
                await _runtime.stop_post_listener(listener_account_id)
            return
        if not _runtime._reconcile_owner_is_current(  # noqa: SLF001
            listener_account_id, generation, reconcile_generation
        ):
            return
        await _runtime._inbox_runtime.start_inbox()  # noqa: SLF001 - peer module
        await _runtime._inbox_runtime.ensure_backfill(  # noqa: SLF001
            listener_account_id,
            subscribed,
            plans,
        )
        # The local — not the module set — feeds the logs below, so a pass overlapping ours
        # can never make us report its numbers as our own.
        unwatched = set(channels) - set(subscribed)
        _publish_unwatched(unwatched)
        _runtime._ensure_sweep_running()  # noqa: SLF001 - peer module
        _runtime._ensure_join_running(listener_account_id)  # noqa: SLF001 - peer module
        if unwatched:
            # The only place an operator can learn a channel is dead to the engine.
            await _runtime.log_event(
                "WARNING",
                "neurocomment_channels_unwatched",
                account_id=listener_account_id,
                extra={"count": len(unwatched), "channels": sorted(unwatched)},
            )
        await _runtime.log_event(
            "INFO",
            "neurocomment_runtime_reconciled",
            account_id=listener_account_id,
            extra={"channels": len(subscribed), "unwatched": len(unwatched)},
        )


async def _resubscribe_unwatched(listener_account_id: str) -> None:
    """Re-register the filter once the paced joins drained, so a late join heals the gap.

    ``subscribe_posts`` runs before the joins (it must — reconcile has to return before
    minutes of jittered joins), so a channel the listener has not joined yet cannot
    resolve to a peer id and is left out of the ``NewMessage`` filter. Nothing else brings
    it back: no periodic reconcile exists, and the pool-rebuild hook re-attaches the OLD
    filter. Live evidence: 16 ``neurocomment_listener_channel_unresolved`` rows for one channel
    across two days and several boots, never healing inside a process — and since #279
    that channel is also permanently red in the SPA.

    Re-subscribe only, never reconcile: ``_ensure_join_running`` would see this very task
    alive, queue a rerun and leak the flag into the next pass.

    A failure is logged and contained, never raised: this is the tail of the join task,
    which also owns the rolling-24h cap accounting and the coalesced rerun, so it must not
    die here (a cancel still propagates — ``CancelledError`` is not an ``Exception``). The
    gap report is left exactly as it stood, so the channel keeps reading red in the SPA and
    the next reconcile — any channel edit, Start, or boot — retries the heal.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a load-time import cycle.

    async with _runtime.neurocomment_lifecycle():
        current = await _runtime.get_listener_account_id()
        running = await _runtime.get_listener_running()
        if listener_account_id != _runtime._RUNTIME_ACCOUNT_ID:  # noqa: SLF001
            return
        if (current is not None or running) and (not running or current != listener_account_id):
            return
        if not _runtime._UNWATCHED_CHANNELS:  # noqa: SLF001 - peer module
            return
        generation = _runtime._RUNTIME_GENERATION  # noqa: SLF001
    try:
        channels = (await list_active_watch_channels()).channels
        if not channels:
            return
        plans = await _runtime._inbox_runtime.prepare_backfill_plans(channels)  # noqa: SLF001
        subscribed = await _runtime.subscribe_posts(
            listener_account_id,
            channels,
            _runtime.on_post,
        )
        async with _runtime.neurocomment_lifecycle():
            if not _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
                return
            await _runtime._inbox_runtime.ensure_backfill(  # noqa: SLF001
                listener_account_id,
                subscribed,
                plans,
            )
            _publish_unwatched(set(channels) - set(subscribed))
    except Exception as exc:  # the join task must survive a failed heal.
        logger.exception("resubscribe failed for %s", listener_account_id)
        await _runtime.log_event(
            "WARNING",
            "neurocomment_resubscribe_failed",
            account_id=listener_account_id,
            extra={"error_type": type(exc).__name__},
        )
