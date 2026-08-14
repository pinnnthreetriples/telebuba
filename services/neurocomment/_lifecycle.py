"""Runtime entrypoints — app startup/shutdown hooks, reconcile trigger, status query.

The outer surface other layers call (``main`` app hooks, the ``api`` status
endpoints, ``campaigns`` mutations) lives here to keep
:mod:`services.neurocomment._runtime` under the aislop file-size cap. The core
listener/task machinery (on-post, reconcile, start, sweep + onboarding task
lifecycle) stays in ``_runtime``; these functions call back into it via the
module object (``_runtime.reconcile_neurocomment_runtime`` etc.) so tests that
monkeypatch those attributes still see the patch. Re-exported into ``_runtime``
so ``_runtime.<name>`` and the package re-exports resolve unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.config import settings
from core.db import (
    get_listener_account_id,
    get_listener_running,
    list_active_watch_channels,
)
from schemas.neurocomment import NeurocommentRuntimeStatus
from services.neurocomment import _signals, _state, _sweep


async def neurocomment_runtime_status() -> NeurocommentRuntimeStatus:
    """Fleet runtime state for the UI: is the engine subscribed, and over how many channels.

    ``running`` reflects the persisted ``listener_running`` flag (actively
    subscribed), not merely whether an account is remembered. The remembered
    ``listener_account_id`` is always returned when one is set, so a *paused*
    runtime shows the listener strip with ``running=False`` — the SPA tells "paused
    with a remembered listener" from "no listener" by that field being non-null.
    The watch set is only read when running, so a paused/stopped engine costs two
    scalar reads. ``unwatched_channels`` reports the requested channels the listener
    could not resolve — they are excluded from ``active_channels`` so the SPA never
    claims to watch a channel whose posts can never arrive. A listener that reconcile
    had to unsubscribe because its account is warming publishes the WHOLE watch set as
    unwatched (it clears no ``running`` flag — the operator paused nothing), so
    ``running=True`` with ``active_channels == 0`` is the honest "up but deaf" report.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a parent import cycle.

    log_limit = settings.neurocomment.log_limit
    listener_account_id = await get_listener_account_id()
    running = await get_listener_running()
    onboarding = _runtime.is_onboarding_running()
    if not running:
        return NeurocommentRuntimeStatus(
            running=False,
            listener_account_id=listener_account_id,
            log_limit=log_limit,
            onboarding=onboarding,
        )
    channels = (await list_active_watch_channels()).channels
    # Channels the listener could not resolve are requested but NOT watched, so they must
    # not inflate the count. Read from the last reconcile's in-memory set (no round-trip)
    # and intersected with the current watch set, so a since-unlinked channel drops out.
    unwatched = sorted(_runtime._UNWATCHED_CHANNELS.intersection(channels))  # noqa: SLF001 - peer module
    return NeurocommentRuntimeStatus(
        running=True,
        active_channels=len(channels) - len(unwatched),
        unwatched_channels=unwatched,
        listener_account_id=listener_account_id,
        log_limit=log_limit,
        onboarding=onboarding,
    )


async def reconcile_if_running() -> None:
    """Re-point the live listener at the current watch set — no-op when not running.

    Called after a channel link/unlink so the running listener's subscription tracks
    the DB immediately, instead of only at the next start/boot. Gated on
    ``listener_running`` so a *paused* runtime (id remembered, flag off) is not
    silently resubscribed by a channel edit. Also (re)triggers campaign onboarding —
    a campaign edited after Start would otherwise never get readiness rows.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a parent import cycle.

    async with _runtime.neurocomment_lifecycle():
        if not await get_listener_running():
            return
        listener_account_id = await get_listener_account_id()
        if listener_account_id is not None:
            generation = _runtime._activate_runtime_owner(listener_account_id)  # noqa: SLF001
        else:
            return
    await _runtime.reconcile_neurocomment_runtime(listener_account_id)
    async with _runtime.neurocomment_lifecycle():
        if _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
            _runtime._ensure_onboarding_running(  # noqa: SLF001 - peer module
                _signals.signal_onboarding_progress,
            )


async def reconcile_neurocomment_on_startup() -> None:
    """No-arg ``app.on_startup`` hook: resume the listener only if it was running.

    A remembered-but-*paused* listener (``listener_account_id`` set,
    ``listener_running`` False) stays paused across a reboot — resuming it would
    silently re-enable a runtime the operator turned off (audit 2026-07-02).

    Stale claims are reclaimed unconditionally first. The sweep runs that same pass on
    every tick now, but only ever for a RUNNING listener, so this call is what still
    covers a runtime that boots paused: a crash mid-post otherwise leaves rows stuck
    ``claimed`` (quota spent, the post_id un-claimable) until an operator hits Start.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a parent import cycle.

    await _reclaim_stale_claims_on_startup()
    await _runtime._inbox_runtime.recover_inbox()  # noqa: SLF001
    # Rehydrate cooldowns unconditionally (#34) — a just-flooded account stays parked
    # across a restart even for a runtime that boots paused.
    await _state.hydrate_cooldowns()
    async with _runtime.neurocomment_lifecycle():
        if not await get_listener_running():
            return
        listener_account_id = await get_listener_account_id()
        if listener_account_id is not None:
            generation = _runtime._activate_runtime_owner(listener_account_id)  # noqa: SLF001
        else:
            return
    await _runtime.reconcile_neurocomment_runtime(listener_account_id)
    async with _runtime.neurocomment_lifecycle():
        if _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
            # Resume onboarding too: campaigns created since the last Start would
            # otherwise boot with a live listener but zero readiness rows.
            _runtime._ensure_onboarding_running(  # noqa: SLF001 - peer module
                _signals.signal_onboarding_progress,
            )


async def _reclaim_stale_claims_on_startup() -> None:
    """Run the sweep's stale-claim pass once at boot, before the running-gate decides.

    The pass itself lives with the sweep's other passes, so the cutoff and the log line
    can't drift between the two triggers; this is only the boot one. Both are needed: the
    sweep task exists only while the listener runs, and a runtime that boots paused or
    stopped would otherwise carry a crash's orphans until an operator hit Start.
    """
    await _sweep._reclaim_stale_claims(datetime.now(UTC))  # noqa: SLF001 - peer module


async def shutdown_neurocomment_on_shutdown() -> None:
    """No-arg ``app.on_shutdown`` hook: tear the listener + tasks down on exit."""
    from services.neurocomment import (  # noqa: PLC0415 - avoid a parent import cycle.
        _discovery_state,
        _runtime,
    )

    # Unconditional: a discovery run can be serving a campaign account with no
    # listener configured, so it must not be gated on the listener branch below.
    await _discovery_state.shutdown_discovery_runs()
    async with _runtime.neurocomment_lifecycle():
        listener_account_id = await get_listener_account_id()
        if listener_account_id is not None:
            await _runtime.shutdown_neurocomment_runtime(listener_account_id)
