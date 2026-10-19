"""A mobile stint's cycle: the account's Redroid pod instead of Telethon actions.

Taken only when the loop task is bound to the mobile device and the emulator is on
(``_loop._execute_cycle``); a desktop stint, or a mobile one with the emulator off,
keeps the Telethon cycle. One cycle opens the pod in the background (booting it when
needed, and signing in an app it finds signed out with a login code read through the
account's existing session), spends the visit's time in the app, and stops the pod again, whatever
happened. A visit that went well parks the pod instead (``docker pause``: no CPU, its
memory kept), and the account's next open thaws it with no boot; a pod parked
``pause_ttl_seconds`` is stopped, unless someone opened it since. Stops run on their
own, so a Stop of the warming never waits for Docker. At most ``max_running_pods``
warming pods run or wait parked at once; a cycle finding none free stops the longest
parked pod first, and is skipped (``emulator_capacity``) when none is parked: a full
host is not the account's failure.

A pod that cannot open or a session that fails is a failed cycle with the refusal
code as its detail, never a fallback to Telethon. A passing cause (a timeout, a
login-code pause) sleeps it off and retries; anything else, or
``_FAILURE_LIMIT`` failures in a row, parks the account in ``error``. A pod the
operator holds, no free slot and a session that found nothing to do are skipped
cycles, not the account's failures. A failed session keeps what it did before failing.

The day's totals keep their columns: channels read go to ``reads``, reactions to
``reactions``; stories, scrolls and the seconds spent go to the log event only.
The failure streaks and slots are process-local, like the rest of the runtime state.
"""

from __future__ import annotations

import asyncio
import itertools
from typing import TYPE_CHECKING

from core.config import settings
from core.db import load_warming_settings
from core.logging import log_event
from schemas.emulator import PlaybookPlan
from schemas.warming import WarmingCycleResult
from services.accounts._result import AccountActionError
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    open_account_mobile,
    phone_activity,
    run_mobile_activity,
    stop_account_pod,
)
from services.accounts.mobile_pods import park_account_pod, stop_parked_pod
from services.warming import _seams
from services.warming._phases import persona_reaction_probability
from services.warming._snapshot import current_snapshot
from services.warming._transitions import MOBILE_REFUSED_ACTION, MOBILE_SESSION_ACTION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from schemas.emulator import EmulatorRefusalCode, PlaybookResult
    from schemas.warming import WarmingCycleRequest

_NOT_SIGNED_IN = "emulator_not_signed_in"
_CAPACITY: EmulatorRefusalCode = "emulator_capacity"
# Causes a later cycle may not meet again; every other code needs the operator.
_PASSING_CODES = frozenset(
    {
        "emulator_busy",
        "emulator_command_failed",
        "emulator_command_timeout",
        "emulator_boot_timeout",
        "emulator_code_timeout",
        "emulator_not_running",
        "device_code_cooldown",
        "device_primary_unavailable",
    }
)
# Failed cycles in a row after which even a passing cause parks the account.
_FAILURE_LIMIT = 5
# One budgeted action buys this many seconds in the app; a cycle gets at least
# the floor, and never more than the emulator's session cap.
_SECONDS_PER_ACTION = 30
_MIN_SECONDS = 60
# The chance a visit searches the app for a channel, by persona; a glance never does,
# nor looks at photos and notes.
_SEARCH_CHANCE = {"calm": 0.15, "normal": 0.3, "active": 0.5}

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


def session_seconds(budget: int | None) -> int:
    """The seconds a visit with ``budget`` actions spends in the app."""
    cap = settings.emulator.activity_max_seconds
    if budget is None:
        return cap
    return min(cap, max(_MIN_SECONDS, budget * _SECONDS_PER_ACTION))


async def session_plan(request: WarmingCycleRequest) -> PlaybookPlan:
    """The visit's shape for the app, as the Telethon cycle would walk it.

    Its channels and stories order, the persona's reaction chance (none while the
    operator turned reactions off), stories only while story viewing is on, and the
    visit's action budget as the session's cap. Beyond a glance it looks at photos and
    notes, and with the persona's ``_SEARCH_CHANCE`` searches for one of the
    configured words. It never joins, makes folders or opens a Mini App: those
    change the account and stay off until the operator asks for them.
    """
    snapshot = current_snapshot(request.account_id)
    controls = snapshot.controls if snapshot else await load_warming_settings()
    visit = request.visit
    reaction = persona_reaction_probability(request.activity_persona)
    glance = visit is not None and visit.kind == "glance"
    searching = not glance and _seams.rng.random() < _SEARCH_CHANCE[request.activity_persona]
    return PlaybookPlan(
        channels=visit.channels if visit else None,
        reaction_probability=reaction if controls.reactions_enabled else 0.0,
        stories=settings.warming.story_view_enabled,
        stories_first=visit.stories_first if visit else False,
        max_actions=request.remaining_actions,
        media=not glance,
        search_queries=[_seams.rng.choice(settings.emulator.search_queries)] if searching else [],
    )


async def run_mobile_cycle(
    request: WarmingCycleRequest,
    *,
    on_step: Callable[[str], Awaitable[None]],
) -> WarmingCycleResult:
    """Open the pod, spend the visit in the app, park or stop the pod; never raises a refusal.

    A pod busy with the operator's own open or session is left running: it is theirs.
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
    try:
        opened = await open_account_mobile(account_id, background=True)
        if not opened.signed_in:
            return await _log_failure(account_id, _NOT_SIGNED_IN, None)
        await on_step("read")
        activity = await run_mobile_activity(account_id, seconds, await session_plan(request))
        went_well = True
    except EmulatorBusyError:
        owned = False
        return await _log_skip(account_id, "emulator_busy")
    except AccountActionError as exc:
        return await _log_failure(account_id, exc.code, getattr(exc, "partial", None))
    finally:
        # An operator who took the phone's controls meanwhile keeps it running: theirs.
        if not owned or phone_activity(account_id) == "manual":
            _RUNNING.discard(account_id)
        else:
            _detach(_park(account_id) if went_well else _stop_and_free(account_id))
    _STREAKS.pop(account_id, None)
    await log_event(
        "INFO",
        "warming_mobile_cycle_done",
        account_id=account_id,
        extra={"seconds": seconds, **activity.model_dump(mode="json")},
    )
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
        attempted_actions=activity.channels_read + activity.reactions,
    )


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
        if not owned or phone_activity(account_id) == "manual":
            _RUNNING.discard(account_id)
        else:
            _detach(_park(account_id) if signed_in else _stop_and_free(account_id))
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


async def _stop_and_free(account_id: str) -> None:
    try:
        await stop_account_pod(account_id)
    finally:
        _RUNNING.discard(account_id)


async def _park(account_id: str) -> None:
    """Park the pod for the next visit; stop it once parked ``pause_ttl_seconds``.

    A pod that cannot be parked, or a TTL of 0, is stopped at once. A later visit
    takes the pod back by popping its token, which this timer then no longer finds.
    """
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
    read = partial.channels_read if partial is not None else 0
    reactions = partial.reactions if partial is not None else 0
    return WarmingCycleResult(
        account_id=account_id,
        status="failed",
        detail=code,
        failures=1,
        channels_read=read,
        reactions_sent=reactions,
        attempted_actions=read + reactions,
        last_failed_action=MOBILE_SESSION_ACTION if retry else MOBILE_REFUSED_ACTION,
    )
