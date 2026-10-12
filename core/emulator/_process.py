"""Run one console command (ldconsole / adb) as an argv list, bounded and killable."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import TYPE_CHECKING

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
    an abandoned ``adb install`` or ``ldconsole launch`` must not keep running.
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
