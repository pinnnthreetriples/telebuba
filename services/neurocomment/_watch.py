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

from typing import TYPE_CHECKING

from core.db import list_active_watch_channels

if TYPE_CHECKING:
    from collections.abc import Iterable


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

    # Warming and neurocomment are mutually exclusive per account. This is the
    # single choke point every subscription path funnels through (start, channel
    # edit, startup resume), so the guard lives here — start_neurocomment adds an
    # early raise on top for the interactive 409. A warming listener is unsubscribed
    # (never re-subscribed) rather than raising, so boot/channel-edit stay safe.
    if listener_account_id in await _runtime.list_warming_account_ids():
        await _runtime.stop_post_listener(listener_account_id)
        await _runtime._stop_sweep()  # noqa: SLF001 - peer module
        await _runtime._stop_join()  # noqa: SLF001 - peer module
        # This path leaves ``listener_running`` set (the operator paused nothing), so the
        # status query keeps reporting a running engine over a listener that is DOWN.
        # Every requested channel is unwatched here — reporting the whole set is the only
        # answer that cannot paint a green strip over a dead listener.
        _publish_unwatched((await list_active_watch_channels()).channels)
        await _runtime.log_event(
            "WARNING",
            "neurocomment_listener_warming_skipped",
            account_id=listener_account_id,
        )
        return
    channels = (await list_active_watch_channels()).channels
    if not channels:
        await _runtime.stop_post_listener(listener_account_id)
        await _runtime._stop_sweep()  # noqa: SLF001 - peer module
        await _runtime._stop_join()  # noqa: SLF001 - peer module
        _publish_unwatched()  # unsubscribed → no channel is "requested but missing"
        return
    subscribed = await _runtime.subscribe_posts(listener_account_id, channels, _runtime.on_post)
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

    if not _runtime._UNWATCHED_CHANNELS:  # noqa: SLF001 - peer module
        return
    try:
        channels = (await list_active_watch_channels()).channels
        if not channels:
            return
        subscribed = await _runtime.subscribe_posts(
            listener_account_id,
            channels,
            _runtime.on_post,
        )
        _publish_unwatched(set(channels) - set(subscribed))
    except Exception as exc:  # noqa: BLE001 - the join task must survive a failed heal.
        await _runtime.log_event(
            "WARNING",
            "neurocomment_resubscribe_failed",
            account_id=listener_account_id,
            extra={"error_type": type(exc).__name__, "message": str(exc)},
        )
