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

from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import (
    get_listener_account_id,
    get_listener_running,
    list_active_watch_channels,
    reclaim_stale_claims,
)
from core.logging import log_event
from schemas.neurocomment import NeurocommentRuntimeStatus
from services.neurocomment import _signals, _state


async def neurocomment_runtime_status() -> NeurocommentRuntimeStatus:
    """Fleet runtime state for the UI: is the engine subscribed, and over how many channels.

    ``running`` reflects the persisted ``listener_running`` flag (actively
    subscribed), not merely whether an account is remembered. The remembered
    ``listener_account_id`` is always returned when one is set, so a *paused*
    runtime shows the listener strip with ``running=False`` — the SPA tells "paused
    with a remembered listener" from "no listener" by that field being non-null.
    The watch set is only read when running, so a paused/stopped engine costs two
    scalar reads.
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
    return NeurocommentRuntimeStatus(
        running=True,
        active_channels=len(channels),
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

    if not await get_listener_running():
        return
    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await _runtime.reconcile_neurocomment_runtime(listener_account_id)
        _runtime._ensure_onboarding_running(_signals.signal_onboarding_progress)  # noqa: SLF001 - peer module


async def reconcile_neurocomment_on_startup() -> None:
    """No-arg ``app.on_startup`` hook: resume the listener only if it was running.

    A remembered-but-*paused* listener (``listener_account_id`` set,
    ``listener_running`` False) stays paused across a reboot — resuming it would
    silently re-enable a runtime the operator turned off (audit 2026-07-02).

    Stale claims are reclaimed unconditionally first: a crash mid-post leaves rows
    stuck ``claimed`` forever (the post_id is then permanently un-claimable), and
    that must be cleaned up even for a runtime that boots paused.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a parent import cycle.

    await _reclaim_stale_claims_on_startup()
    # Rehydrate cooldowns unconditionally (#34) — a just-flooded account stays parked
    # across a restart even for a runtime that boots paused.
    await _state.hydrate_cooldowns()
    if not await get_listener_running():
        return
    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await _runtime.reconcile_neurocomment_runtime(listener_account_id)
        # Resume onboarding too: campaigns created since the last Start would
        # otherwise boot with a live listener but zero readiness rows.
        _runtime._ensure_onboarding_running(_signals.signal_onboarding_progress)  # noqa: SLF001 - peer module


async def _reclaim_stale_claims_on_startup() -> None:
    """Mark claims stuck 'claimed' since before the reclaim cutoff as 'failed'."""
    cutoff = (
        datetime.now(UTC) - timedelta(seconds=settings.neurocomment.stale_claim_reclaim_seconds)
    ).isoformat()
    reclaimed = await reclaim_stale_claims(cutoff)
    if reclaimed:
        await log_event("INFO", "neurocomment_stale_claims_reclaimed", extra={"count": reclaimed})


async def shutdown_neurocomment_on_shutdown() -> None:
    """No-arg ``app.on_shutdown`` hook: tear the listener + tasks down on exit."""
    from services.neurocomment import _runtime  # noqa: PLC0415 - avoid a parent import cycle.

    listener_account_id = await get_listener_account_id()
    if listener_account_id is not None:
        await _runtime.shutdown_neurocomment_runtime(listener_account_id)
