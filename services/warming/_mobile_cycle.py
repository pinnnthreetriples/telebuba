"""A mobile stint's cycle: the account's Redroid pod instead of Telethon actions.

Taken only when the loop task is bound to the mobile device and the emulator is on
(``_loop._execute_cycle``); a desktop stint, or a mobile one with the emulator off,
keeps the Telethon cycle. One cycle opens the pod in the background (booting it when
needed, and signing in an app it finds signed out with a login code read through the
account's existing session), spends the visit's time in the app, and stops the pod again, whatever
happened. A visit that went well parks the pod instead (``docker pause``: no CPU, its
memory kept), and the account's next open thaws it with no boot; a pod parked
``pause_ttl_seconds`` is stopped, unless someone opened it since. Stops run on their
own, so a Stop of the warming never waits for Docker; a pod the operator took as the
visit ended (their open, controls or hold) is left to them, neither parked nor stopped.
At most ``max_running_pods`` warming pods run or wait parked at once; a cycle finding
none free stops the longest parked pod first, and is skipped (``emulator_capacity``)
when none is parked: a full host is not the account's failure.

A pod that cannot open or a session that fails is a failed cycle with the refusal
code as its detail, never a fallback to Telethon. A passing cause (a timeout, a
login-code pause, a screen it could not read, an app that stayed offline) sleeps it
off and retries; anything else, or ``_FAILURE_LIMIT`` failures in a row, parks the
account in ``error``. Telegram's limit dialog in the app is a ``flood_wait`` of
unknown length, as on desktop. A pod the operator holds, no free slot and a session
that found nothing to do are skipped cycles, not the account's failures. A failed or
cancelled session keeps what it did spent (``spent_actions``).

The day's totals keep their columns: channels read go to ``reads``, reactions to
``reactions``; stories, scrolls and the seconds spent go to the log event only. A
phone that found no channel to read says so on its card until it reads one.
The failure streaks and slots are process-local, like the rest of the runtime state.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator.playbook import spent_actions
from core.logging import log_event
from schemas.emulator import PlaybookResult
from schemas.warming import WarmingCycleResult
from services.accounts._result import AccountActionError
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    open_account_mobile,
    open_mobile_visit,
    operator_holds,
    phone_activity,
    run_mobile_activity,
    stop_account_pod,
)
from services.accounts.mobile_pods import park_account_pod, stop_parked_pod
from services.warming import _mobile_prep_state
from services.warming._mobile_plan import session_plan, session_seconds
from services.warming._transitions import MOBILE_REFUSED_ACTION, MOBILE_SESSION_ACTION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from schemas.device_farm import PhoneStatus
    from schemas.emulator import EmulatorRefusalCode
    from schemas.warming import WarmingCycleRequest
    from services.warming._steps import _ChannelTally

_NOT_SIGNED_IN = "emulator_not_signed_in"
_CAPACITY: EmulatorRefusalCode = "emulator_capacity"
_APP_OFFLINE: EmulatorRefusalCode = "emulator_app_offline"
# Causes a later cycle may not meet again; every other code needs the operator.
_PASSING_CODES = frozenset(
    {
        "emulator_busy",
        "emulator_command_failed",
        "emulator_command_timeout",
        "emulator_boot_timeout",
        "emulator_code_timeout",
        "emulator_not_running",
        "emulator_field_missing",
        "emulator_screen_timeout",
        _APP_OFFLINE,
        "device_code_cooldown",
        "device_primary_unavailable",
    }
)
# Failed cycles in a row after which even a passing cause parks the account.
_FAILURE_LIMIT = 5
# A phone being prepared waits this long between tries for a free slot.
_SLOT_WAIT_SECONDS = 30
# ...and gives up after this long: the job fails as ``emulator_capacity`` on the card.
_SLOT_WAIT_LIMIT_SECONDS = 30 * 60

_STREAKS: dict[str, int] = {}
# Accounts whose warming pod holds a slot, running or parked, until its stop is done.
_RUNNING: set[str] = set()
# Parked pods, oldest first, each with the token of the timer that may stop it.
_PARKED: dict[str, int] = {}
_TOKENS = itertools.count()
# Strong references to the detached pod stops; the event loop holds tasks weakly.
_DETACHED: set[asyncio.Task[None]] = set()
# What the operator holds a pod by once a visit is over: their controls, or their open.
_HANDED_OVER: frozenset[PhoneStatus] = frozenset({"manual", "starting"})


async def run_mobile_cycle(
    request: WarmingCycleRequest,
    *,
    on_step: Callable[[str], Awaitable[None]],
    tally: _ChannelTally | None = None,
) -> WarmingCycleResult:
    """Open the pod, spend the visit in the app, park or stop the pod; never raises a refusal.

    A pod busy with the operator's own open or session is left running: it is theirs.
    Cancelled mid-session, what the phone already did goes on ``tally``: it stays spent.
    """
    account_id = request.account_id
    # Its own parked pod holds its slot already; the open thaws it.
    holding = _PARKED.pop(account_id, None) is not None or account_id in _RUNNING
    if not holding and not await _free_slot():
        return await _log_skip(account_id, _CAPACITY)
    _RUNNING.add(account_id)
    seconds = session_seconds(request.remaining_actions)
    owned = True
    went_well = False
    progress = PlaybookResult()
    try:
        serial = await open_mobile_visit(account_id)
        if serial is None:
            return await _log_failure(account_id, _NOT_SIGNED_IN, None)
        await on_step("read")
        plan = await session_plan(request)
        # The open just checked the pod and put the app in front: the session goes on from there.
        activity = await run_mobile_activity(
            account_id, seconds, plan, opened=serial, progress=progress
        )
        went_well = True
    except asyncio.CancelledError:
        if tally is not None:
            tally.attempts += spent_actions(progress)
        raise
    except EmulatorBusyError:
        owned = False
        return await _log_skip(account_id, "emulator_busy")
    except AccountActionError as exc:
        return await _log_failure(account_id, exc.code, getattr(exc, "partial", None))
    finally:
        if owned:
            _detach(_park(account_id) if went_well else _stop_and_free(account_id))
        else:
            _RUNNING.discard(account_id)
    return await _settle(account_id, seconds, activity)


async def _settle(account_id: str, seconds: int, activity: PlaybookResult) -> WarmingCycleResult:
    """The cycle's result from what a session that ran in the app did and met."""
    _note_channels(account_id, activity)
    if "app_offline" in activity.skipped:
        return await _log_failure(account_id, _APP_OFFLINE, activity)
    _STREAKS.pop(account_id, None)
    await log_event(
        "INFO",
        "warming_mobile_cycle_done",
        account_id=account_id,
        extra={"seconds": seconds, **activity.model_dump(mode="json")},
    )
    if "app_error" in activity.skipped:
        return _limited(account_id, activity)
    if not (
        activity.channels_read
        or activity.reactions
        or activity.stories_watched
        or activity.searches_done
        or activity.media_consumed
    ):
        # Nothing to do in the app: not a warming day, and not the account's failure.
        return WarmingCycleResult(account_id=account_id, status="skipped", detail="no activity")
    return WarmingCycleResult(
        account_id=account_id,
        status="ok",
        channels_read=activity.channels_read,
        reactions_sent=activity.reactions,
        attempted_actions=spent_actions(activity),
    )


def _limited(account_id: str, activity: PlaybookResult) -> WarmingCycleResult:
    """Telegram's limit dialog: a flood wait of unknown length, the desktop's fallback."""
    return WarmingCycleResult(
        account_id=account_id,
        status="flood_wait",
        channels_read=activity.channels_read,
        reactions_sent=activity.reactions,
        attempted_actions=spent_actions(activity),
        last_failed_action=MOBILE_SESSION_ACTION,
    )


def _note_channels(account_id: str, activity: PlaybookResult) -> None:
    """Flag the card of a phone that had no channel to read; a read lifts it."""
    if activity.channels_read:
        _mobile_prep_state.NO_CHANNELS.discard(account_id)
    elif "no_channel" in activity.skipped:
        _mobile_prep_state.NO_CHANNELS.add(account_id)


async def prepare_pod(account_id: str) -> bool:
    """Boot the account's pod and sign its app in, as a visit's open does; whether it is.

    Takes a warming slot like a visit, waiting ``_SLOT_WAIT_SECONDS`` between tries
    while the host is full (``emulator_capacity`` after ``_SLOT_WAIT_LIMIT_SECONDS``),
    and gives it back the same way: a signed-in pod is parked
    for the first visit, any other is stopped. Raises the open's refusal, and
    ``EmulatorBusyError`` while the operator holds the phone (left running: theirs).
    """
    holding = _PARKED.pop(account_id, None) is not None or account_id in _RUNNING
    give_up = asyncio.get_running_loop().time() + _SLOT_WAIT_LIMIT_SECONDS
    while not holding:
        if await _free_slot():
            break
        if asyncio.get_running_loop().time() >= give_up:
            raise EmulatorRefusedError(_CAPACITY)
        await asyncio.sleep(_SLOT_WAIT_SECONDS)
    _RUNNING.add(account_id)
    owned = True
    signed_in = False
    try:
        signed_in = (await open_account_mobile(account_id, background=True)).signed_in
    except EmulatorBusyError:
        owned = False
        raise
    finally:
        if owned:
            _detach(_park(account_id) if signed_in else _stop_and_free(account_id))
        else:
            _RUNNING.discard(account_id)
    return signed_in


async def _free_slot() -> bool:
    """Whether a pod may start: below the cap, or once the longest parked pod is stopped."""
    if len(_RUNNING) < settings.emulator.max_running_pods:
        return True
    if not _PARKED:
        return False
    oldest = next(iter(_PARKED))
    del _PARKED[oldest]
    try:
        await stop_parked_pod(oldest)
    finally:
        _RUNNING.discard(oldest)
    return len(_RUNNING) < settings.emulator.max_running_pods


def _detach(work: Coroutine[object, object, None]) -> None:
    """Run ``work`` in a task of its own, held strongly until it is done."""
    task = asyncio.ensure_future(work)
    _DETACHED.add(task)
    task.add_done_callback(_DETACHED.discard)


def _handed_over(account_id: str) -> bool:
    """Whether the operator took the pod since the visit: their controls, open or hold.

    Asked right before the pod's lock is taken, with no await between: an open the
    operator starts later finds the lock held and is busy, never paused under them.
    """
    return phone_activity(account_id) in _HANDED_OVER or operator_holds(account_id)


async def _stop_and_free(account_id: str) -> None:
    try:
        if not _handed_over(account_id):
            await stop_account_pod(account_id)
    finally:
        _RUNNING.discard(account_id)


async def _park(account_id: str) -> None:
    """Park the pod for the next visit; stop it once parked ``pause_ttl_seconds``.

    A pod the operator took meanwhile is left to them. A pod that cannot be parked, or a
    TTL of 0, is stopped at once. A later visit
    takes the pod back by popping its token, which this timer then no longer finds.
    """
    if _handed_over(account_id):
        _RUNNING.discard(account_id)
        return
    ttl = settings.emulator.pause_ttl_seconds
    if not ttl or not await park_account_pod(account_id):
        await _stop_and_free(account_id)
        return
    token = next(_TOKENS)
    _PARKED[account_id] = token
    await asyncio.sleep(ttl)
    if _PARKED.get(account_id) != token:
        return
    del _PARKED[account_id]
    try:
        await stop_parked_pod(account_id)
    finally:
        _RUNNING.discard(account_id)


async def _log_skip(account_id: str, code: str) -> WarmingCycleResult:
    await log_event(
        "INFO", "warming_mobile_cycle_skipped", account_id=account_id, extra={"reason": code}
    )
    return WarmingCycleResult(account_id=account_id, status="skipped", detail=code)


async def _log_failure(
    account_id: str, code: str, partial: PlaybookResult | None
) -> WarmingCycleResult:
    streak = _STREAKS.get(account_id, 0) + 1
    retry = code in _PASSING_CODES and streak < _FAILURE_LIMIT
    if retry:
        _STREAKS[account_id] = streak
    else:
        _STREAKS.pop(account_id, None)
    await log_event(
        "WARNING",
        "warming_mobile_cycle_failed",
        account_id=account_id,
        extra={"reason": code, "retry": retry, "streak": streak},
    )
    done = partial if partial is not None else PlaybookResult()
    return WarmingCycleResult(
        account_id=account_id,
        status="failed",
        detail=code,
        failures=1,
        channels_read=done.channels_read,
        reactions_sent=done.reactions,
        attempted_actions=spent_actions(done),
        last_failed_action=MOBILE_SESSION_ACTION if retry else MOBILE_REFUSED_ACTION,
    )
