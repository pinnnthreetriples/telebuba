"""Read the phone's Telegram past what it shows over its chat list (``_mobile_start``'s read).

A signed-in app may show Android's permission dialog (denied by its resource-id: the
contacts are never shared), Telegram's own contacts prompt (put off «not now»), or a
screen of its own nobody here knows, such as an open chat: BACK is pressed there a few
times at most, the app put back in front after each. The screen it then shows is the
answer, and only a sign-in screen means signed out. A screen still unknown is
``emulator_screen_unknown``; an app that gives no dump at all is
``emulator_app_unresponsive`` (no key is sent to it: a frozen app only dies of one),
restarted by ``restarted_if_frozen`` (twice at most).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.emulator import (
    EmulatorError,
    bring_app_to_front,
    deny_permission,
    go_back,
    put_off_contacts,
    restart_app,
    wait_screen,
)
from core.logging import log_event

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from collections.abc import Set as AbstractSet

    from core.emulator.ui_dump import Screen, UiNode
    from schemas.emulator import EmulatorRefusalCode

APP_UNRESPONSIVE: EmulatorRefusalCode = "emulator_app_unresponsive"
_SCREEN_UNKNOWN: EmulatorRefusalCode = "emulator_screen_unknown"

SIGN_IN_SCREENS: frozenset[Screen] = frozenset({"intro", "phone", "confirm", "code", "password"})
# The screens a read ends on: the chat list or a sign-in screen.
SETTLED: frozenset[Screen] = frozenset({"chats", *SIGN_IN_SCREENS})
# Shown over the chat list and answered on the way to it.
_PROMPTS: frozenset[Screen] = frozenset({"permission", "contacts"})
# BACK presses on an unknown screen, and screens handled in all, before it is a refusal.
_BACKS = 3
_STEPS = 6
# Restarts of a frozen app before it is a refusal.
_RESTARTS = 2


async def settled_screen(
    serial: str,
    until: AbstractSet[Screen] = SETTLED,
    *,
    seen: tuple[Screen, list[UiNode]] | None = None,
) -> Screen:
    """The screen the app settles on once its prompts are answered; ``seen`` was just read.

    It waits for one of ``until``; a settled screen the wait ran out on (a code screen
    that stayed up) is answered as it is, for the caller to judge.
    """
    backs = 0
    screen: Screen = "unknown"
    for _ in range(_STEPS):
        screen, nodes = seen or await wait_screen(serial, {*until, *_PROMPTS})
        seen = None
        if screen in SETTLED:
            return screen
        if screen == "permission":
            await deny_permission(serial, nodes)
        elif screen == "contacts":
            await put_off_contacts(serial, nodes)
        elif screen == "unknown" and backs < _BACKS:
            backs += 1
            await go_back(serial)
            await bring_app_to_front(serial)  # a BACK on the chat list leaves the app
        else:
            break
    raise EmulatorError(APP_UNRESPONSIVE if screen == "unresponsive" else _SCREEN_UNKNOWN)


async def restarted_if_frozen[T](
    account_id: str, serial: str, attempt: Callable[[], Awaitable[T]]
) -> T:
    """``attempt``, and again after a restart of the app each time it froze (no dump).

    Telegram on redroid's guest GPU can deadlock right after «Start Messaging»: it stays
    alive but gives no dump, and any input then kills it. A relaunch comes back, but it
    can freeze again at once: the app is restarted twice at most, a third freeze is the
    refusal.
    """
    for _ in range(_RESTARTS):
        try:
            return await attempt()
        except EmulatorError as exc:
            if exc.code != APP_UNRESPONSIVE:
                raise
        await log_event(
            "WARNING",
            "account_mobile_app_restarted",
            account_id=account_id,
            extra={"reason": APP_UNRESPONSIVE},
        )
        await restart_app(serial)
    return await attempt()
