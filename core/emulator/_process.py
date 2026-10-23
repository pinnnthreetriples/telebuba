"""Run one console command (docker / adb) as an argv list, bounded and killable."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_COMMAND_TIMEOUT: EmulatorRefusalCode = "emulator_command_timeout"
_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"

# Stdlib sink, step names only: the argv itself is never logged (see ``EmulatorError``).
logger = logging.getLogger(__name__)


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Make sure the child is gone, even while the caller is being cancelled."""
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    await asyncio.shield(process.wait())


async def run_command(argv: Sequence[str], *, seconds: float, step: str) -> str:
    """Run ``argv`` without a shell and return its stdout.

    A missing binary, a timeout and a non-zero exit each raise ``EmulatorError`` with
    their own code. A cancelled caller kills the child before the cancellation goes on:
    an abandoned ``adb install`` or ``docker start`` must not keep running.
    """
    try:
        process = await asyncio.create_subprocess_exec(  # nosec B603 - argv list, no shell
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise EmulatorError(_EMULATOR_NOT_FOUND) from exc
    except OSError as exc:
        logger.warning("emulator step %s could not start (%s)", step, type(exc).__name__)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc
    try:
        async with asyncio.timeout(seconds):
            stdout, _stderr = await process.communicate()
    except TimeoutError as exc:
        await _kill(process)
        logger.warning("emulator step %s timed out", step)
        raise EmulatorError(_EMULATOR_COMMAND_TIMEOUT) from exc
    except asyncio.CancelledError:
        await _kill(process)
        raise
    if process.returncode != 0:
        logger.warning("emulator step %s exited with %s", step, process.returncode)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return stdout.decode("utf-8", errors="replace")


async def docker(*args: str, seconds: float | None = None) -> str:
    """One docker CLI call, bounded like every other console step (or by ``seconds``)."""
    return await run_command(
        [str(settings.emulator.docker_path), *args],
        seconds=seconds or settings.emulator.command_timeout_seconds,
        step=f"docker {args[0]}",
    )


async def docker_or_none(*args: str, seconds: float | None = None) -> str | None:
    """``docker(*args)``, or ``None`` when docker ran and said no (a non-zero exit).

    For the calls whose refusal is an answer: a network that is not there yet, a
    probe of a tunnel still coming up. A missing docker or a timeout still raises.
    """
    try:
        return await docker(*args, seconds=seconds)
    except EmulatorError as exc:
        if exc.code != _EMULATOR_COMMAND_FAILED:
            raise
        return None


async def adb_command(*args: str, seconds: float | None = None, step: str) -> str:
    """One adb CLI call (``adb <args>``), bounded like every other console step."""
    return await run_command(
        [str(settings.emulator.adb_path), *args],
        seconds=seconds or settings.emulator.command_timeout_seconds,
        step=step,
    )


async def shell(serial: str, *args: str, seconds: float | None = None) -> str:
    """Run an adb shell command on ``serial``."""
    return await adb_command(
        "-s", serial, "shell", *args, seconds=seconds, step=f"adb shell {args[0]}"
    )
