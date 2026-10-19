"""The web screen: scrcpy-server runs on the phone for one viewer, and only then.

``open_screen`` pushes the server, forwards a host port to its abstract socket,
starts it through ``adb shell`` and connects the video socket (then the control one,
in ``control`` mode). Nothing encodes before that and nothing after: leaving the
context closes the sockets, waits for the server to exit, kills it by its ``scid``
when it does not, and removes the forward. A cancelled viewer cleans up the same way.

The video socket is passed on as scrcpy writes it (codec header, then a 12-byte
header per frame): the browser's decoder parses it. The server keeps its frame
headers (no ``raw_stream``): without them the decoder cannot tell a keyframe.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import run_command
from core.emulator._scrcpy_control import encode_command, read_device_message
from core.emulator.adb import connect_device
from core.emulator.assets import ensure_scrcpy_server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from schemas.device_farm import ScreenCommand
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"
_EMULATOR_SCREEN_TIMEOUT: EmulatorRefusalCode = "emulator_screen_timeout"

_DEVICE_PATH = "/data/local/tmp/telebuba-scrcpy-server.jar"
_SERVER_CLASS = "com.genymobile.scrcpy.Server"
_LOOPBACK = "127.0.0.1"
_CHUNK = 1 << 16
_RETRY_SECONDS = 0.1
# How long a server whose sockets were closed gets to exit on its own.
_EXIT_GRACE_SECONDS = 2.0

# Stdlib sink, step names only: an adb argv is never logged (see ``EmulatorError``).
logger = logging.getLogger(__name__)


class ScreenStream:
    """One viewer's sockets: video bytes out, commands in, clipboard text back."""

    def __init__(
        self,
        video: asyncio.StreamReader,
        control: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None,
    ) -> None:
        self._video = video
        self._control = control

    @property
    def controllable(self) -> bool:
        return self._control is not None

    async def read_video(self) -> bytes:
        """The next bytes of the video socket; ``b""`` once the server is gone."""
        return await self._video.read(_CHUNK)

    async def send(self, command: ScreenCommand) -> None:
        if self._control is None:
            raise EmulatorError(_EMULATOR_COMMAND_FAILED)
        writer = self._control[1]
        writer.write(encode_command(command))
        await writer.drain()

    async def read_clipboard(self) -> str | None:
        """The next clipboard text the phone sends; ``None`` once the socket closes.

        Waits forever in ``view`` mode, where there is no control socket.
        """
        if self._control is None:
            await asyncio.Event().wait()
            return None
        while True:
            try:
                text = await read_device_message(self._control[0])
            except (asyncio.IncompleteReadError, ConnectionError, ValueError):
                return None
            if text is not None:
                return text


def server_arguments(scid: str, *, control: bool) -> list[str]:
    """``app_process`` arguments for the server: one viewer, video only, h264."""
    emulator = settings.emulator
    return [
        f"CLASSPATH={_DEVICE_PATH}",
        "app_process",
        "/",
        _SERVER_CLASS,
        emulator.scrcpy_server_version,
        f"scid={scid}",
        "log_level=warn",
        "tunnel_forward=true",
        "audio=false",
        f"control={'true' if control else 'false'}",
        "video_codec=h264",
        f"max_size={emulator.stream_max_size}",
        f"max_fps={emulator.stream_max_fps}",
        f"video_bit_rate={emulator.stream_bit_rate}",
        "send_device_meta=false",
        "clipboard_autosync=false",
        "power_off_on_close=false",
    ]


async def _adb(serial: str, *args: str) -> str:
    return await run_command(
        [str(settings.emulator.adb_path), "-s", serial, *args],
        seconds=settings.emulator.command_timeout_seconds,
        step=f"adb {args[0]}",
    )


async def _forward(serial: str, scid: str) -> int:
    """A free host port forwarded to the server's socket (adb picks it: ``tcp:0``)."""
    answer = await _adb(serial, "forward", "tcp:0", f"localabstract:scrcpy_{scid}")
    port = answer.strip()
    if not port.isdigit():
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return int(port)


async def _start_server(serial: str, scid: str, *, control: bool) -> asyncio.subprocess.Process:
    argv = [
        str(settings.emulator.adb_path),
        "-s",
        serial,
        "shell",
        *server_arguments(scid, control=control),
    ]
    try:
        return await asyncio.create_subprocess_exec(  # nosec B603 - argv list, no shell
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise EmulatorError(_EMULATOR_NOT_FOUND) from exc
    except OSError as exc:
        logger.warning("scrcpy-server could not start (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc


async def _connect_video(
    port: int, server: asyncio.subprocess.Process
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """The video socket, once the server answers it with its dummy byte.

    adb accepts the forwarded connection even before the server listens and then
    drops it, so a connection counts only once that first byte arrives.
    """
    try:
        async with asyncio.timeout(settings.emulator.stream_connect_timeout_seconds):
            return await _probe_video(port, server)
    except TimeoutError as exc:
        # Also covers a connection adb keeps open without ever passing the byte.
        raise EmulatorError(_EMULATOR_SCREEN_TIMEOUT) from exc


async def _probe_video(
    port: int, server: asyncio.subprocess.Process
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    while True:
        if server.returncode is not None:
            raise EmulatorError(_EMULATOR_COMMAND_FAILED)
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection(_LOOPBACK, port)
            await reader.readexactly(1)
        except (OSError, asyncio.IncompleteReadError):
            if writer is not None:
                writer.close()  # not awaited: the deadline's cancel must not be swallowed
            await asyncio.sleep(_RETRY_SECONDS)
            continue
        except BaseException:
            # Cancelled mid-probe: this socket is not in ``_stop``'s list yet.
            if writer is not None:
                writer.close()
            raise
        return reader, writer


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with suppress(OSError, asyncio.CancelledError):
        await writer.wait_closed()


async def _stop(
    serial: str,
    scid: str,
    port: int,
    server: asyncio.subprocess.Process | None,
    writers: list[asyncio.StreamWriter],
) -> None:
    """Close the sockets, make sure the server is gone, remove the forward."""
    for writer in writers:
        await _close_writer(writer)
    if server is not None:
        try:
            async with asyncio.timeout(_EXIT_GRACE_SECONDS):
                await server.wait()
        except TimeoutError:
            logger.warning("scrcpy-server outlived its sockets; killing it")
            with suppress(EmulatorError):
                await _adb(serial, "shell", "pkill", "-f", f"scid={scid}")
            if server.returncode is None:
                with suppress(ProcessLookupError):
                    server.kill()
            await server.wait()
    with suppress(EmulatorError):
        await _adb(serial, "forward", "--remove", f"tcp:{port}")


@asynccontextmanager
async def open_screen(serial: str, *, control: bool) -> AsyncIterator[ScreenStream]:
    """Run scrcpy-server on ``serial`` for as long as the context is open.

    A missing server file is downloaded first (``core.emulator.assets``).
    """
    server_file = await ensure_scrcpy_server()
    scid = f"{secrets.randbits(31):08x}"
    await connect_device(serial)
    await _adb(serial, "push", str(server_file), _DEVICE_PATH)
    port = await _forward(serial, scid)
    server: asyncio.subprocess.Process | None = None
    writers: list[asyncio.StreamWriter] = []
    try:
        server = await _start_server(serial, scid, control=control)
        video, video_writer = await _connect_video(port, server)
        writers.append(video_writer)
        channel: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        if control:
            try:
                channel = await asyncio.open_connection(_LOOPBACK, port)
            except OSError as exc:
                raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc
            writers.append(channel[1])
        yield ScreenStream(video, channel)
    finally:
        # Shielded, and awaited to the end even when cancelled: whoever comes next may
        # rely on this server being gone once the context has exited.
        stopping = asyncio.ensure_future(_stop(serial, scid, port, server, writers))
        cancelled = False
        while not stopping.done():
            try:
                await asyncio.shield(stopping)
            except asyncio.CancelledError:
                cancelled = True  # a repeated cancel (shutdown) still waits it out
        if cancelled:
            raise asyncio.CancelledError
        stopping.result()
