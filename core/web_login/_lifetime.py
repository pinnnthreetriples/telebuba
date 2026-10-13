"""What a spawned browser inherits from the backend, and how it dies with it.

Three guards around the process :func:`core.web_login.browser.launch_account_web`
spawns, split out of that module for its file-size budget:

* :func:`browser_env` — Chrome starts with an allow-listed environment. ``core.config``
  loads ``.env`` into ``os.environ``, so an inherited environment hands every app
  setting (the Telegram API hash, the auth secret, the database URL, proxy defaults) to
  Chrome and to every helper process it starts.
* :func:`tie_to_backend` — on Windows each browser joins one Job Object that kills its
  members when its last handle closes. Only this process holds that handle, so a
  backend that crashes or is killed takes its windows with it instead of leaving them
  running with no DevTools client — i.e. with the fingerprint already stripped, the
  operator's real machine one reload away from Telegram, and the profile still claimed
  so the next open of that account fails as a hand-off.
* :func:`close_orphaned_browsers` — the sweep for windows a previous run left anyway
  (one predating the Job Object, or a child that broke away from it). A browser is
  only ever closed through the DevTools endpoint ITS profile advertises, after the same
  ownership proof the launcher uses, so a Chrome the operator runs outside Telebuba —
  which has no such file in our profile root — is never touched.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

import httpx

from core.web_login._cdp import CdpError, CdpSession

if TYPE_CHECKING:
    from pathlib import Path

# What Chrome needs to start on Windows: system and profile locations, the temp dirs,
# the executable search path and the processor description. Nothing app-specific is
# on the list, so no setting loaded from ``.env`` can reach the browser. ``HOME`` is
# for a non-Windows host, where it plays USERPROFILE's part.
_ENV_ALLOWLIST = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "COMPUTERNAME",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "NUMBER_OF_PROCESSORS",
        "OS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "PUBLIC",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
    }
)

# Win32 constants (winnt.h / processthreadsapi.h).
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
# A child Chrome starts with CREATE_BREAKAWAY_FROM_JOB would otherwise fail to start
# at all. One that breaks away still dies with its browser: every Chrome child exits
# when its IPC channel to the browser process closes.
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_INHERIT_HANDLE = False

# The backend's one job, created on first use and never closed: the kernel closes the
# handle when this process ends, however it ends, and that is the whole mechanism.
_job: dict[str, int] = {}

# Chrome writes the port on line 1 and the browser target path on line 2.
_ACTIVE_PORT_FILE = "DevToolsActivePort"
_ACTIVE_PORT_LINES = 2
# Loopback: a live endpoint answers at once and a dead one is refused at once.
_SWEEP_HTTP_TIMEOUT = 2.0


def browser_env() -> dict[str, str]:
    """The allow-listed slice of this process's environment, for the browser."""
    return {key: value for key, value in os.environ.items() if key.upper() in _ENV_ALLOWLIST}


def _create_kill_on_close_job() -> int:
    """A new Job Object that ends every member when its last handle is closed."""
    # Inline, not a helper: it is also what tells the type checker the rest is Win32.
    if sys.platform != "win32":
        msg = "Job Objects exist on Windows only."
        raise OSError(msg)
    import ctypes  # noqa: PLC0415 - Windows-only, never imported elsewhere
    from ctypes import wintypes  # noqa: PLC0415

    class _BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = tuple((name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f"))

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = _ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_BREAKAWAY_OK
    )
    if not kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(wintypes.HANDLE(job))
        raise error
    return int(job)


def _assign_to_job(job: int, pid: int) -> None:
    """Put process ``pid`` into ``job``; its later children join it automatically."""
    # Inline, not a helper: it is also what tells the type checker the rest is Win32.
    if sys.platform != "win32":
        msg = "Job Objects exist on Windows only."
        raise OSError(msg)
    import ctypes  # noqa: PLC0415 - Windows-only, never imported elsewhere
    from ctypes import wintypes  # noqa: PLC0415

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, _INHERIT_HANDLE, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(process)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(process))


def tie_to_backend(pid: int) -> None:
    """Make browser ``pid`` end when this backend process ends (Windows only).

    Called straight after the spawn. Chrome starts its GPU, network and renderer
    processes only after it has initialised, so they are created inside the job; one
    created first would still exit once the browser it talks to is gone.
    """
    if sys.platform != "win32":
        return
    if "handle" not in _job:
        _job["handle"] = _create_kill_on_close_job()
    _assign_to_job(_job["handle"], pid)


async def close_orphaned_browsers(profiles_root: Path) -> int:
    """Close every browser a previous run left open on a profile under ``profiles_root``.

    Returns how many were closed. Runs at startup, before any window of this run
    exists, so every live endpoint found here is a leftover. A stale
    ``DevToolsActivePort`` (its browser is gone) is removed so nothing reads it later.

    Concurrently: on Windows a connect to a dead loopback port is retried rather than
    refused at once, so each stale profile costs up to the whole timeout, and in
    sequence that delay would grow with the number of accounts.
    """
    profiles = await asyncio.to_thread(_profiles_with_endpoints, profiles_root)
    async with httpx.AsyncClient(trust_env=False, timeout=_SWEEP_HTTP_TIMEOUT) as client:
        closed = await asyncio.gather(*(_sweep_profile(client, *profile) for profile in profiles))
    return sum(closed)


async def _sweep_profile(client: httpx.AsyncClient, profile: Path, port: int, target: str) -> int:
    """Close the profile's browser if it is still ours to close; 1 if it was."""
    ws_url = await _owned_endpoint(client, port, target)
    if ws_url is not None:
        await _close_browser(ws_url)
    with suppress(OSError):
        await asyncio.to_thread((profile / _ACTIVE_PORT_FILE).unlink)
    return int(ws_url is not None)


def _profiles_with_endpoints(profiles_root: Path) -> list[tuple[Path, int, str]]:
    """``(profile, port, browser target path)`` for each profile that advertises one."""
    try:
        profiles = [item for item in profiles_root.iterdir() if item.is_dir()]
    except OSError:
        return []
    found: list[tuple[Path, int, str]] = []
    for profile in profiles:
        try:
            lines = (profile / _ACTIVE_PORT_FILE).read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        if len(lines) >= _ACTIVE_PORT_LINES and lines[0].strip().isdigit():
            found.append((profile, int(lines[0].strip()), lines[1].strip()))
    return found


async def _owned_endpoint(client: httpx.AsyncClient, port: int, target: str) -> str | None:
    """The browser WebSocket on ``port`` if it is the target the profile names, else None.

    The port alone proves nothing — after a restart anything may hold it — so the
    endpoint must name the very browser target the profile's file recorded.
    """
    try:
        payload = (await client.get(f"http://127.0.0.1:{port}/json/version")).json()
        ws_url = str(payload.get("webSocketDebuggerUrl") or "")
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    if not target or not ws_url.endswith(target):
        return None
    return ws_url


async def _close_browser(ws_url: str) -> None:
    """Ask the browser to close; it may drop the socket before it answers."""
    try:
        session = await CdpSession.connect(ws_url)
    except (CdpError, OSError, TimeoutError):
        return
    try:
        with suppress(CdpError, OSError, TimeoutError):
            await session.send_command("Browser.close")
    finally:
        with suppress(Exception):
            await session.aclose()
