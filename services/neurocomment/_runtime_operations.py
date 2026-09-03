"""Start/stop and background-owner operations for the neurocomment runtime."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.db import set_listener_account_id, set_listener_running
from services import _account_owner
from services.neurocomment import _discovery_state, _signals
from services.neurocomment._onboarding_owner import generation_fence

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.neurocomment_progress import OnboardingProgressEvent


class ListenerBusyDiscoveryError(Exception):
    """Raised when a channel-discovery run is already reading with the picked account.

    The likeliest sequence of all, because a run can only START while the listener is
    stopped: the operator stops the listener, searches, then presses Start again while
    the run is still going — and the post listener resolves peers and joins on the very
    session the run is paced-reading with. The generation fence does not help: it is a
    ContextVar set inside the listener's own task context, so a discovery task spawned
    from an API request reads ``None`` and every assertion on it passes.

    Defined here, beside its only raise site, rather than next to
    ``ListenerBusyWarmingError`` in ``_runtime``: that module is 11 lines under the size
    gate's warn threshold and this class is 12 lines long.
    """


class ListenerBusyNeuroshillingError(Exception):
    """Raised when a running neuroshilling campaign already holds the picked account.

    The reciprocal of the ``account_is_listener`` refusal ``_claim_accounts`` answers
    with: neuroshilling will not start a campaign on the running listener, and the
    listener will not be pointed at an account a campaign is playing. Without this half
    a campaign's account could be made the listener mid-run, which is two features
    talking on one Telegram session — a flood report and then a ban.

    Defined here beside its raise site for the same reason as the class above.
    """


async def shutdown_neurocomment_runtime(listener_account_id: str) -> None:
    """Fence the owner, stop producers, then boundedly drain owned workers."""
    from services.neurocomment import _runtime  # noqa: PLC0415

    _runtime._invalidate_runtime_owner(listener_account_id)  # noqa: SLF001
    await _runtime._stop_post_listener(listener_account_id)  # noqa: SLF001
    await _runtime._inbox_runtime.stop_inbox()  # noqa: SLF001
    await _runtime._stop_sweep()  # noqa: SLF001
    await _runtime._stop_onboarding()  # noqa: SLF001
    await _runtime._stop_join()  # noqa: SLF001
    _runtime._publish_unwatched_runtime()  # noqa: SLF001
    await _runtime._cancel_bounded(*list(_runtime._TASKS))  # noqa: SLF001


async def _refuse_if_busy(listener_account_id: str) -> None:
    """The three runtimes that can already be holding this account's Telegram session.

    The caller holds ``neurocomment_lifecycle()`` and ``account_lock(listener_account_id)``:
    each check is only worth the lock that stops a claim landing just after it. The
    discovery claim is in-process and synchronous, so it cannot be straddled; and
    ``_claim_accounts`` takes the same per-account lock across its own listener read and
    its claim, so a campaign cannot publish one between these checks and the caller's write.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415

    if listener_account_id in await _runtime._list_warming_account_ids():  # noqa: SLF001
        raise _runtime.ListenerBusyWarmingError(listener_account_id)
    if _discovery_state.account_busy(listener_account_id):
        raise ListenerBusyDiscoveryError(listener_account_id)
    if _account_owner.owner_of(listener_account_id) == "neuroshilling":
        raise ListenerBusyNeuroshillingError(listener_account_id)


async def start_neurocomment(
    listener_account_id: str,
    *,
    on_progress: Callable[[OnboardingProgressEvent], None] | None = None,
) -> None:
    """Commit the listener owner, reconcile live delivery, and start onboarding."""
    from services.neurocomment import _runtime  # noqa: PLC0415
    from services.warming import account_lock  # noqa: PLC0415

    async with _runtime.neurocomment_lifecycle(), account_lock(listener_account_id):
        await _refuse_if_busy(listener_account_id)
        previous = await _runtime._runtime_get_listener_account_id()  # noqa: SLF001
        if previous is not None and previous != listener_account_id:
            _runtime._invalidate_runtime_owner(previous)  # noqa: SLF001
            await _runtime._stop_post_listener(previous)  # noqa: SLF001
        generation = _runtime._activate_runtime_owner(listener_account_id)  # noqa: SLF001
        await set_listener_account_id(listener_account_id)
        await set_listener_running(running=True)

    await _runtime._reconcile_runtime(listener_account_id)  # noqa: SLF001
    async with _runtime.neurocomment_lifecycle():
        if not _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
            return
        _runtime._ensure_onboarding_running(  # noqa: SLF001
            on_progress or _signals.signal_onboarding_progress,
        )


def is_onboarding_running() -> bool:
    from services.neurocomment import _runtime  # noqa: PLC0415

    return _runtime._ONBOARD_TASK is not None and not _runtime._ONBOARD_TASK.done()  # noqa: SLF001


def _ensure_onboarding_running(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    account_id = _runtime._RUNTIME_ACCOUNT_ID  # noqa: SLF001
    if account_id is None:
        return
    owner = (account_id, _runtime._RUNTIME_GENERATION)  # noqa: SLF001
    if _runtime._ONBOARD_TASK is not None and not _runtime._ONBOARD_TASK.done():  # noqa: SLF001
        if owner == _runtime._ONBOARD_TASK_OWNER:  # noqa: SLF001
            _runtime._ONBOARD_RERUN = True  # noqa: SLF001
            return
        _runtime._ONBOARD_TASK.cancel()  # noqa: SLF001
        _runtime._retain_until_done(_runtime._ONBOARD_TASK)  # noqa: SLF001
        _runtime._ONBOARD_RERUN = False  # noqa: SLF001
    _runtime._ONBOARD_TASK = asyncio.create_task(  # noqa: SLF001
        _onboard_active_campaigns(
            on_progress,
            account_id,
            _runtime._RUNTIME_GENERATION,  # noqa: SLF001
        ),
    )
    _runtime._ONBOARD_TASK_OWNER = owner  # noqa: SLF001


async def _onboard_active_campaigns(
    on_progress: Callable[[OnboardingProgressEvent], None] | None,
    owner_account_id: str,
    generation: int,
) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    while True:
        if not _runtime._runtime_owner_is_current(owner_account_id, generation):  # noqa: SLF001
            return
        for campaign in (await _runtime._list_campaigns()).campaigns:  # noqa: SLF001
            if not _runtime._runtime_owner_is_current(owner_account_id, generation):  # noqa: SLF001
                return
            if campaign.status != "active":
                continue
            try:
                with generation_fence(
                    lambda: _runtime._runtime_owner_is_current(  # noqa: SLF001
                        owner_account_id,
                        generation,
                    ),
                ):
                    await _runtime._onboard_campaign(  # noqa: SLF001
                        campaign.campaign_id,
                        on_progress=on_progress,
                    )
            except Exception as exc:  # noqa: BLE001 - isolate one campaign
                await _runtime._runtime_log_event(  # noqa: SLF001
                    "ERROR",
                    "neurocomment_start_onboard_failed",
                    extra={"campaign_id": campaign.campaign_id, "error_type": type(exc).__name__},
                )
        if not _runtime._ONBOARD_RERUN:  # noqa: SLF001
            return
        _runtime._ONBOARD_RERUN = False  # noqa: SLF001


def _ensure_join_running(listener_account_id: str) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    generation = _runtime._activate_runtime_owner(listener_account_id)  # noqa: SLF001
    if _runtime._JOIN_TASK is not None and not _runtime._JOIN_TASK.done():  # noqa: SLF001
        if (listener_account_id, generation) == _runtime._JOIN_TASK_OWNER:  # noqa: SLF001
            _runtime._JOIN_RERUN = True  # noqa: SLF001
            return
        _runtime._JOIN_TASK.cancel()  # noqa: SLF001
        _runtime._retain_until_done(_runtime._JOIN_TASK)  # noqa: SLF001
    _runtime._JOIN_TASK = asyncio.create_task(  # noqa: SLF001
        _join_watch_channels(listener_account_id, generation),
    )
    _runtime._JOIN_TASK_OWNER = (listener_account_id, generation)  # noqa: SLF001


async def _join_watch_channels(listener_account_id: str, generation: int) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    while True:
        if not _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
            return
        await _runtime._run_join_pass(listener_account_id, generation=generation)  # noqa: SLF001
        if not _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
            return
        if not _runtime._JOIN_RERUN:  # noqa: SLF001
            break
        _runtime._JOIN_RERUN = False  # noqa: SLF001
    if _runtime._runtime_owner_is_current(listener_account_id, generation):  # noqa: SLF001
        await _runtime._resubscribe_runtime(listener_account_id)  # noqa: SLF001


async def _teardown_listener_locked(listener_account_id: str, *, clear_account: bool) -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415
    from services.warming import account_lock  # noqa: PLC0415

    async with _runtime.neurocomment_lifecycle(), account_lock(listener_account_id):
        try:
            await _runtime.shutdown_neurocomment_runtime(listener_account_id)
        finally:
            if clear_account:
                await set_listener_account_id(None)
            await set_listener_running(running=False)


async def stop_neurocomment() -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    async with _runtime.neurocomment_lifecycle():
        listener_account_id = await _runtime._runtime_get_listener_account_id()  # noqa: SLF001
        if listener_account_id is None:
            _runtime._invalidate_runtime_owner()  # noqa: SLF001
            await set_listener_running(running=False)
            return
        await _teardown_listener_locked(listener_account_id, clear_account=False)


async def remember_neurocomment_listener(listener_account_id: str) -> bool:
    """Persist the picked listener without starting anything ("Сохранить" in the modal).

    Returns ``False`` without writing when the engine is running: re-pointing a live
    listener is an ownership hand-off, and ``start_neurocomment`` is the only thing that
    performs one. The read and the write share the lifecycle lock, so a Start cannot land
    between them and leave the pointer naming an account no runtime owner ever took.

    Refuses a busy account on the same terms as Start, even though nothing is started
    here: a saved pointer is not inert. The listener is the natural pick for channel
    discovery whether or not the engine runs, so a pointer at a warming or campaign-held
    session would put a multi-minute keyword stream on a session another runtime owns.
    """
    from services.neurocomment import _runtime  # noqa: PLC0415
    from services.warming import account_lock  # noqa: PLC0415

    async with _runtime.neurocomment_lifecycle(), account_lock(listener_account_id):
        await _refuse_if_busy(listener_account_id)
        if await _runtime.get_listener_running():
            return False
        await set_listener_account_id(listener_account_id)
        return True


async def clear_neurocomment_listener() -> None:
    from services.neurocomment import _runtime  # noqa: PLC0415

    async with _runtime.neurocomment_lifecycle():
        listener_account_id = await _runtime._runtime_get_listener_account_id()  # noqa: SLF001
        if listener_account_id is None:
            _runtime._invalidate_runtime_owner()  # noqa: SLF001
            await set_listener_account_id(None)
            await set_listener_running(running=False)
            return
        await _teardown_listener_locked(listener_account_id, clear_account=True)
