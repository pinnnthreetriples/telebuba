"""A phone's web screen: one viewer at a time, scrcpy running only while one watches.

* The phone must already run: watching never starts a pod (the start is minutes; the
  farm's Start button does that).
* One viewer per phone: a new one supersedes the current one, which is told so
  (``screen_superseded``) and gone before the new server starts — two encoders on
  one phone would cost twice the CPU for a picture nobody sees twice. A viewer
  superseded while still waiting for its turn never starts a server at all.
* ``control`` holds the phone's controls (``claim_manual``): refused while a start or
  an activity session holds it, and an activity session is refused while it is held.
  ``view`` takes nothing and is how the warming board watches a stint.
* Leaving the session, however it ends, stops the server and releases the controls.

The registry is process-local, like the rest of the runtime state (one uvicorn worker).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from pydantic import TypeAdapter, ValidationError

from core.config import settings
from core.emulator import EmulatorError, open_screen
from core.logging import log_event
from schemas.device_farm import ScreenCommand
from services.accounts.mobile_emulator import (
    EmulatorBusyError,
    EmulatorRefusedError,
    claim_manual,
    phone_activity,
    release_manual,
    running_serial,
)
from services.device_farm._phones import account_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from core.emulator import ScreenStream
    from schemas.device_farm import PhoneStatus, ScreenMode
    from schemas.emulator import EmulatorRefusalCode

_DISABLED: EmulatorRefusalCode = "mobile_emulator_disabled"
_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
# What holds a phone for longer than a viewer: only its own end frees it for control.
_NOT_HANDED_OVER: frozenset[PhoneStatus] = frozenset({"starting", "warming"})

_COMMAND = TypeAdapter[ScreenCommand](ScreenCommand)


class ScreenSupersededError(Exception):
    """A newer viewer took the phone while this one was still waiting for its turn."""


class ScreenCommandError(ValueError):
    """A viewer sent something that is not one of the screen's commands."""


def parse_command(text: str) -> ScreenCommand:
    """One viewer text frame as a command; ``ScreenCommandError`` for anything else."""
    try:
        return _COMMAND.validate_json(text)
    except (ValidationError, json.JSONDecodeError) as exc:
        raise ScreenCommandError from exc


class _Viewer:
    def __init__(self) -> None:
        self.superseded = asyncio.Event()
        self.done = asyncio.Event()


_VIEWERS: dict[str, _Viewer] = {}


class ScreenSession:
    """What the transport needs from one open screen."""

    def __init__(self, stream: ScreenStream, viewer: _Viewer) -> None:
        self._stream = stream
        self._viewer = viewer

    @property
    def controllable(self) -> bool:
        return self._stream.controllable

    async def read_video(self) -> bytes:
        return await self._stream.read_video()

    async def read_clipboard(self) -> str | None:
        return await self._stream.read_clipboard()

    async def send(self, command: ScreenCommand) -> None:
        try:
            await self._stream.send(command)
        except (EmulatorError, ConnectionError) as exc:
            raise EmulatorRefusedError(_COMMAND_FAILED) from exc

    async def wait_superseded(self) -> None:
        await self._viewer.superseded.wait()


async def _take_over(phone_id: str) -> _Viewer:
    """Register a new viewer, after the current one (if any) has let go."""
    previous = _VIEWERS.get(phone_id)
    viewer = _Viewer()
    _VIEWERS[phone_id] = viewer
    if previous is not None:
        previous.superseded.set()
        # No cap of its own: the previous viewer's open and cleanup are bounded by
        # their own timeouts, and going ahead before it is gone would mean two servers
        # on one phone or a control claim it still holds.
        await previous.done.wait()
    return viewer


@asynccontextmanager
async def screen_session(phone_id: str, mode: ScreenMode) -> AsyncIterator[ScreenSession]:
    """Open the phone's screen for one viewer; refusals are ``EmulatorRefusedError``."""
    account_id = account_of(phone_id)
    if not settings.emulator.enabled:
        raise EmulatorRefusedError(_DISABLED)
    serial = await running_serial(account_id)
    if mode == "control" and phone_activity(account_id) in _NOT_HANDED_OVER:
        # Refused before the current viewer is superseded: a refused take-over must
        # not end someone else's watching of the stint.
        raise EmulatorBusyError
    viewer = await _take_over(phone_id)
    claimed = False
    try:
        if viewer.superseded.is_set():
            # A third viewer came while this one waited: it never starts a server.
            raise ScreenSupersededError
        if mode == "control":
            claim_manual(account_id)
            claimed = True
        try:
            async with open_screen(serial, control=mode == "control") as stream:
                if viewer.superseded.is_set():
                    # Superseded while opening: closed before it is ever shown.
                    raise ScreenSupersededError
                await log_event(
                    "INFO", "phone_screen_opened", account_id=account_id, extra={"mode": mode}
                )
                yield ScreenSession(stream, viewer)
        except EmulatorError as exc:
            raise EmulatorRefusedError(exc.code) from exc
    finally:
        if claimed:
            release_manual(account_id)
        viewer.done.set()
        if _VIEWERS.get(phone_id) is viewer:
            del _VIEWERS[phone_id]
