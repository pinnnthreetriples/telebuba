"""LDPlayer 9's console (``ldconsole.exe``): find, create, start, stop and remove containers.

A container is found again by its NAME (prefix + account id), and its adb serial is
derived from the index ``list2`` reports — never assumed to be the first emulator's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import run_command

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"

# ``list2`` columns: index, title, top window, bind window, android started, pid, ...
_LIST2_MIN_COLUMNS = 5
# LDPlayer gives instance N the adb port 5555 + 2N, i.e. serial ``emulator-(5554 + 2N)``.
_FIRST_CONSOLE_PORT = 5554


class Instance(NamedTuple):
    index: int
    name: str
    running: bool


def adb_serial(index: int) -> str:
    return f"emulator-{_FIRST_CONSOLE_PORT + 2 * index}"


def parse_list2(output: str) -> list[Instance]:
    instances: list[Instance] = []
    for line in output.splitlines():
        columns = line.strip().split(",")
        if len(columns) < _LIST2_MIN_COLUMNS or not columns[0].isdigit():
            continue
        instances.append(Instance(int(columns[0]), columns[1], columns[4] == "1"))
    return instances


async def _console(*args: str) -> str:
    return await run_command(
        [str(settings.emulator.ldconsole_path), *args],
        seconds=settings.emulator.command_timeout_seconds,
        step=f"ldconsole {args[0]}",
    )


async def find_instance(name: str) -> Instance | None:
    return next((i for i in parse_list2(await _console("list2")) if i.name == name), None)


async def create_instance(name: str, spec: ContainerSpec, template: str | None) -> Instance:
    """A new container named ``name``, sized from settings, presenting ``spec``'s phone.

    ``template`` is a clean container of ``spec``'s Android version to copy; without
    one LDPlayer's default image is used and the boot-time SDK check decides.
    """
    if template:
        await _console("copy", "--name", name, "--from", template)
    else:
        await _console("add", "--name", name)
    instance = await find_instance(name)
    if instance is None:
        raise EmulatorError(_EMULATOR_COMMAND_FAILED)
    await _console(
        "modify",
        "--index",
        str(instance.index),
        "--resolution",
        settings.emulator.resolution,
        "--cpu",
        str(settings.emulator.cpu_cores),
        "--memory",
        str(settings.emulator.memory_mb),
        "--manufacturer",
        spec.manufacturer,
        "--model",
        spec.model,
        "--imei",
        spec.imei,
    )
    return instance


async def launch_instance(index: int) -> None:
    await _console("launch", "--index", str(index))


async def set_container_network(index: int, *, online: bool) -> None:
    """Cut or restore the container's network from the host side (``call.network``).

    Needs no booted Android, unlike ``svc`` through adb, so it can go out right after
    ``launch``: an app the boot starts by itself then finds no network either.
    """
    value = "connect" if online else "offline"
    await _console("action", "--index", str(index), "--key", "call.network", "--value", value)


async def quit_instance(index: int) -> None:
    await _console("quit", "--index", str(index))


async def remove_container(name: str) -> bool:
    """Stop and delete the container named ``name``; ``False`` when there is none.

    A host without LDPlayer has no container to remove, so a missing console is
    "none", not an error — deleting an account must not depend on the emulator.
    """
    if not settings.emulator.ldconsole_path.exists():
        return False
    instance = await find_instance(name)
    if instance is None:
        return False
    if instance.running:
        await quit_instance(instance.index)
    await _console("remove", "--index", str(instance.index))
    return True
