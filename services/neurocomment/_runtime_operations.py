"""Start/stop and background-owner operations for the neurocomment runtime."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.db import set_listener_account_id, set_listener_running
from services.neurocomment import _signals
from services.neurocomment._onboarding_owner import generation_fence

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.neurocomment_progress import OnboardingProgressEvent


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


async def start_neurocomment(
    listener_account_id: str,
    *,
    on_progress: Callable[[OnboardingProgressEvent], None] | None = None,
) -> None:
    """Commit the listener owner, reconcile live delivery, and start onboarding."""
    from services.neurocomment import _runtime  # noqa: PLC0415
    from services.warming import account_lock  # noqa: PLC0415

    async with _runtime.neurocomment_lifecycle(), account_lock(listener_account_id):
        if listener_account_id in await _runtime._list_warming_account_ids():  # noqa: SLF001
            raise _runtime.ListenerBusyWarmingError(listener_account_id)
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
