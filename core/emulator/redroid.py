"""redroid under Docker: find, create, start, stop and remove containers; show one in scrcpy.

A container is found again by its NAME (prefix + account id) and has an INDEX, kept
in a docker label, from which its adb serial follows (``127.0.0.1:<first port + N>``,
published on loopback only). Docker has no window, so ``show_instance`` opens a
scrcpy one.

The network is cut inside the container's own network namespace, through
``docker exec`` (root, no adb, so it works before Android is up): every outgoing
packet is dropped except loopback and adbd's replies, so adb keeps working offline.
It cannot be cut before ``docker start`` (nothing runs in the image before init sets up
its libraries, and once netd owns routing a network attached later cannot get a
gateway), so the cut must land before zygote: no app runs before it. ``docker exec``
works ~0.3 s after start, zygote runs at ~1.4-2 s (redroid 13/14 on this host); a cut
that lands later fails, and the start is undone.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import run_command
from core.emulator._profile import aosp_boot_properties

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_DEVICE_UNKNOWN: EmulatorRefusalCode = "emulator_device_unknown"
_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"
_EMULATOR_SDK_MISMATCH: EmulatorRefusalCode = "emulator_sdk_mismatch"

# Every container this app made carries both; the index decides the adb port.
_LABEL = "telebuba.emulator"
_INDEX_LABEL = "telebuba.index"
_LIST_FORMAT = '{{.Label "telebuba.index"}}|{{.Names}}|{{.State}}'
_ADB_PORT = 5555
# Where the container's identity goes (see ``_give_identity``), after this mark.
_IDENTITY_FILE = "/product/etc/build.prop"
_IDENTITY_MARK = "# telebuba: the phone this container presents\n"
# ``docker exec`` can exit 255 while the container is still coming up; the retries
# stay well inside the time before zygote starts.
_NETWORK_ATTEMPTS = 5
_NETWORK_RETRY_SECONDS = 0.3
# Our own chain, jumped to first from OUTPUT: loopback and adbd's replies only. Only
# creating the chain may fail (it exists after an earlier run); any other failed step
# exits non-zero, so a half-built chain is never reported as offline. So does a cut
# that lands once zygote runs: an app could have connected before it.
_OFFLINE_CHAIN = "telebuba_offline"
_GO_OFFLINE = (
    f"for t in /system/bin/iptables /system/bin/ip6tables; do "
    f"$t -w -N {_OFFLINE_CHAIN} 2>/dev/null; "
    f"$t -w -F {_OFFLINE_CHAIN} "
    f"&& $t -w -A {_OFFLINE_CHAIN} -o lo -j ACCEPT "
    f"&& $t -w -A {_OFFLINE_CHAIN} -p tcp --sport {_ADB_PORT} -j ACCEPT "
    f"&& $t -w -A {_OFFLINE_CHAIN} -j DROP "
    f"&& {{ $t -w -C OUTPUT -j {_OFFLINE_CHAIN} 2>/dev/null "
    f"|| $t -w -I OUTPUT 1 -j {_OFFLINE_CHAIN}; }} "
    f"|| exit 1; "
    f"done; "
    f'[ "$(/system/bin/getprop init.svc.zygote)" != running ]'
)
_GO_ONLINE = (
    f"for t in /system/bin/iptables /system/bin/ip6tables; do "
    f"while $t -w -D OUTPUT -j {_OFFLINE_CHAIN} 2>/dev/null; do :; done; "
    f"done"
)

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


class Instance(NamedTuple):
    index: int
    name: str
    running: bool


# One scrcpy window per container, process-local like the rest of the runtime state.
_VIEWERS: dict[str, asyncio.subprocess.Process] = {}
# Picking a free index and creating the container under it is one step.
_CREATE_LOCK = asyncio.Lock()


def adb_serial(index: int) -> str:
    return f"127.0.0.1:{settings.emulator.redroid_first_adb_port + index}"


def parse_list(output: str) -> list[Instance]:
    """``docker ps`` lines (``index|name|state``) of this app's containers."""
    instances: list[Instance] = []
    for line in output.splitlines():
        index, _, rest = line.strip().partition("|")
        name, _, state = rest.partition("|")
        if index.isdigit() and name:
            instances.append(Instance(int(index), name, state == "running"))
    return instances


async def _docker(*args: str, seconds: float | None = None) -> str:
    return await run_command(
        [str(settings.emulator.docker_path), *args],
        seconds=seconds or settings.emulator.command_timeout_seconds,
        step=f"docker {args[0]}",
    )


async def _list() -> list[Instance]:
    return parse_list(
        await _docker("ps", "-a", "--filter", f"label={_LABEL}", "--format", _LIST_FORMAT)
    )


async def find_instance(name: str) -> Instance | None:
    return next((i for i in await _list() if i.name == name), None)


async def _name_of(index: int) -> str:
    instance = next((i for i in await _list() if i.index == index), None)
    if instance is None:
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    return instance.name


def build_prop(spec: ContainerSpec) -> str:
    """The ``build.prop`` that makes Android report ``spec``'s phone, firmware and region.

    ``Build.*`` read these properties (``core.emulator._profile.aosp_boot_properties``);
    set here, they win over the image's own (``redroid``). They cannot go on the
    container's command line: redroid's first-stage init re-splits it on spaces
    (``Pixel 7`` -> ``Pixel``).
    """
    lines = [f"{key}={value}" for key, value in aosp_boot_properties(spec).items()]
    if any(char in line for line in lines for char in "\r\n"):
        raise EmulatorError(_EMULATOR_DEVICE_UNKNOWN)
    return "\n".join(lines) + "\n"


def with_identity(image_prop: str, identity: str) -> str:
    """The product ``build.prop``: the image's own lines, then ``identity`` (ours replaced)."""
    own = image_prop.split(_IDENTITY_MARK, 1)[0]
    if own and not own.endswith("\n"):
        own += "\n"
    return own + _IDENTITY_MARK + identity


def _rewrite(path: Path, identity: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(with_identity(text, identity), encoding="utf-8", newline="\n")


async def _give_identity(name: str, identity: str) -> None:
    """Write ``identity`` (a ``build_prop``) into the stopped container, before it boots.

    init reads the product partition's ``build.prop`` LAST of all, and a property read
    later wins over the same one read earlier: only there do ours beat the image's own
    ``ro.product.vendor.*`` / ``ro.vendor.build.*`` (read from ``/vendor``, after
    ``/system``). The image's lines are kept; our block after the mark replaces the one
    an earlier start wrote.
    """
    with tempfile.TemporaryDirectory(prefix="telebuba-redroid-") as scratch:
        local = Path(scratch) / "build.prop"
        await _docker("cp", f"{name}:{_IDENTITY_FILE}", str(local))
        await asyncio.to_thread(_rewrite, local, identity)
        await _docker("cp", str(local), f"{name}:{_IDENTITY_FILE}")


async def create_instance(name: str, spec: ContainerSpec) -> Instance:
    """A new, stopped container named ``name`` presenting ``spec``'s phone.

    The image is the one configured for ``spec``'s SDK; an SDK without one is refused
    before anything is created. The phone goes in through ``build_prop``; its screen
    and serial number on the command line (neither has a space). redroid has no
    telephony, so ``spec.imei`` has nowhere to go.
    """
    image = settings.emulator.redroid_images.get(spec.sdk)
    if not image:
        raise EmulatorError(_EMULATOR_SDK_MISMATCH)
    identity = build_prop(spec)  # refused before anything is created
    async with _CREATE_LOCK:
        used = {i.index for i in await _list()}
        index = next(n for n in range(len(used) + 1) if n not in used)
        await _docker(
            "create",
            "--name",
            name,
            "--label",
            f"{_LABEL}=1",
            "--label",
            f"{_INDEX_LABEL}={index}",
            "--privileged",
            "--publish",
            f"127.0.0.1:{settings.emulator.redroid_first_adb_port + index}:{_ADB_PORT}",
            "--cpus",
            str(settings.emulator.cpu_cores),
            "--memory",
            f"{settings.emulator.memory_mb}m",
            image,
            f"androidboot.redroid_width={spec.screen.width}",
            f"androidboot.redroid_height={spec.screen.height}",
            f"androidboot.redroid_dpi={spec.screen.dpi}",
            f"androidboot.serialno={spec.serial}",
            "androidboot.use_memfd=true",
        )
    await _give_identity(name, identity)
    return Instance(index, name, running=False)


async def refresh_identity(name: str, spec: ContainerSpec) -> None:
    """Rewrite a stopped container's ``build_prop`` before it boots again.

    The phone is the same (its serial and IMEI follow the account id), but the
    account's proxy may have changed country since, and the timezone and carrier
    follow it.
    """
    await _give_identity(name, build_prop(spec))


async def launch_instance(index: int) -> None:
    """Start the container; adb forgets its old connection to the port first.

    A connection kept from the container's previous run stays ``offline`` and is
    never retried by itself; ``adb.wait_for_boot`` connects afresh.
    """
    await _docker("start", await _name_of(index))
    with suppress(EmulatorError):
        await run_command(
            [str(settings.emulator.adb_path), "disconnect", adb_serial(index)],
            seconds=settings.emulator.command_timeout_seconds,
            step="adb disconnect",
        )


async def set_container_network(index: int, *, online: bool) -> None:
    """Cut or restore the container's network inside its own namespace (see module doc).

    Both scripts are idempotent, so a failed run is retried, briefly (see
    ``_NETWORK_ATTEMPTS``). One that keeps failing raises, and the start is undone.
    """
    script = _GO_ONLINE if online else _GO_OFFLINE
    name = await _name_of(index)
    for attempt in range(_NETWORK_ATTEMPTS):
        try:
            await _docker("exec", name, "/system/bin/sh", "-c", script)
        except EmulatorError as exc:
            if exc.code != _EMULATOR_COMMAND_FAILED or attempt == _NETWORK_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_NETWORK_RETRY_SECONDS)
        else:
            return


async def show_instance(index: int) -> None:
    """Open the container's scrcpy window, unless this process has one open already."""
    name = await _name_of(index)
    viewer = _VIEWERS.get(name)
    if viewer is not None and viewer.returncode is None:
        return
    argv = [
        str(settings.emulator.scrcpy_path),
        "--serial",
        adb_serial(index),
        "--window-title",
        name,
        "--no-audio",
    ]
    try:
        _VIEWERS[name] = await asyncio.create_subprocess_exec(  # nosec B603 - argv list, no shell
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise EmulatorError(_EMULATOR_NOT_FOUND) from exc
    except OSError as exc:
        logger.warning("scrcpy could not start (%s)", type(exc).__name__)
        raise EmulatorError(_EMULATOR_COMMAND_FAILED) from exc


async def _close_viewer(name: str) -> None:
    viewer = _VIEWERS.pop(name, None)
    if viewer is not None and viewer.returncode is None:
        with suppress(ProcessLookupError):
            viewer.kill()
        await asyncio.shield(viewer.wait())


async def quit_instance(index: int) -> None:
    name = await _name_of(index)
    await _close_viewer(name)
    await _docker("stop", name)


async def remove_container(name: str) -> bool:
    """Stop and delete the container named ``name``; ``False`` when there is none.

    A host without Docker has no container to remove, so a missing docker CLI is
    "none", not an error — deleting an account must not depend on the emulator.
    """
    try:
        instance = await find_instance(name)
    except EmulatorError as exc:
        if exc.code == _EMULATOR_NOT_FOUND:
            return False
        raise
    if instance is None:
        return False
    await _close_viewer(name)
    await _docker("rm", "--force", name)
    return True
