"""A mobile account's pod: a net-gateway container and the Android one in its namespace.

The gateway (``core.emulator._gateway``) owns the network: Android is created with
``--network container:<gateway>``, so it has no route of its own, no VPN inside Android
and no ports (the gateway publishes adb at the pod's index). Both containers carry
that index.

``start_pod`` creates what is missing, gives the gateway the CURRENT proxy, starts it
and waits for its tunnel before Android is started: Android's first packet meets the
tunnel or the kill-switch, never a direct route. ``stop_pod`` stops Android, then the
gateway; ``remove_pod`` deletes both. A failed start undoes what it did: the
containers it created are removed, the ones it found are stopped. ``stop_running_pods``
stops every pod this app left running (a crash, a restart), Androids before gateways.

Between close visits ``pause_pod`` freezes Android (``docker pause``: no CPU, its memory
kept) and leaves the gateway up; ``unpause_pod`` thaws it in a moment, with no boot.
A paused container is ended with ``docker kill`` right after it is thawed: a few
seconds of grace would let the app reconnect and show the account online.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from core.emulator._errors import EmulatorError
from core.emulator._gateway import (
    create_gateway,
    ensure_network,
    gateway_name,
    give_proxy,
    proxy_file,
    resolve_address,
    runs_proxy,
    wait_for_tunnel,
)
from core.emulator._process import docker
from core.emulator.images import ensure_pod_images
from core.emulator.redroid import (
    Instance,
    build_prop,
    close_viewer,
    create_instance,
    image_for,
    launch_instance,
    list_pods,
    refresh_identity,
)

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxySettings

_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)

# Picking a free index and creating the gateway that holds it is one step.
_CREATE_LOCK = asyncio.Lock()
# Seconds ``docker stop`` waits for a container's own shutdown before it kills it:
# redroid's init may not act on SIGTERM, and a stop must fit the app's shutdown.
STOP_GRACE_SECONDS = 3


async def _find(name: str) -> tuple[Instance | None, Instance | None]:
    """The pod's Android container and its gateway, each ``None`` when missing."""
    gateway = gateway_name(name)
    androids, gateways = await list_pods()
    return (
        next((a for a in androids if a.name == name), None),
        next((g for g in gateways if g.name == gateway), None),
    )


async def _free_index() -> int:
    androids, gateways = await list_pods()
    used = {i.index for i in (*androids, *gateways)}
    return next(n for n in range(len(used) + 1) if n not in used)


async def start_pod(name: str, spec: ContainerSpec, proxy: ProxySettings) -> Instance:
    """Start the pod whose Android container is ``name``, on ``proxy``; its running Instance.

    The phone and the proxy are refused before anything is created. Whatever of the
    pod is running is stopped first, so the gateway always starts on ``proxy``.
    """
    image_for(spec)
    build_prop(spec)
    proxy_text = proxy_file(proxy)
    address = await resolve_address(proxy.host)
    await ensure_pod_images(spec)  # a pull or build may take minutes: outside the lock
    gateway = gateway_name(name)
    found: set[str] = set()
    created: set[str] = set()
    try:
        async with _CREATE_LOCK:
            android, current = await _find(name)
            if android is not None and current is None:
                # Its namespace went with its gateway: Android is made again in the new one.
                await docker("rm", "--force", name)
                android = None
            if current is None:
                index = await _free_index()
                await ensure_network()
                created.add(gateway)  # before: a cancelled create may have made it
                await create_gateway(gateway, index)
            else:
                index = current.index
        for container in (android, current):
            if container is not None:
                found.add(container.name)
                await _halt(container)
        await give_proxy(gateway, proxy_text, address)
        await docker("start", gateway)
        await wait_for_tunnel(gateway)
        if android is None:
            created.add(name)  # before: a failed identity copy leaves it half made
            await create_instance(name, spec, index=index, gateway=gateway)
        else:
            await refresh_identity(name, spec, index)  # the proxy may have moved country
        await launch_instance(name, index)
    except BaseException:
        await asyncio.shield(_undo((name, gateway), found=found, created=created))
        raise
    return Instance(index, name, running=True)


async def _stop(container: str) -> None:
    await docker("stop", "--time", str(STOP_GRACE_SECONDS), container)


async def _halt(container: Instance) -> None:
    """Stop a running container; thaw a paused one and kill it at once."""
    if container.paused:
        await docker("unpause", container.name)
        await docker("kill", container.name)
    elif container.running:
        await _stop(container.name)


def _up(container: Instance | None) -> bool:
    return container is not None and (container.running or container.paused)


async def _undo(containers: tuple[str, ...], *, found: set[str], created: set[str]) -> None:
    for container in containers:
        if container not in created | found:
            continue
        try:
            if container in created:
                await docker("rm", "--force", container)
            else:
                await _stop(container)
        except EmulatorError as exc:
            logger.warning("pod cleanup of %s failed (%s)", container, exc.code)


async def _stop_each(containers: list[Instance]) -> EmulatorError | None:
    """Halt every container in ``containers`` at once; the first failure, after all were tried."""
    results = await asyncio.gather(*(_halt(c) for c in containers), return_exceptions=True)
    failures: list[EmulatorError] = []
    for container, result in zip(containers, results, strict=True):
        if isinstance(result, EmulatorError):
            logger.warning("stopping %s failed (%s)", container.name, result.code)
            failures.append(result)
        elif isinstance(result, BaseException):
            raise result
    return failures[0] if failures else None


def _started_at(stamp: str) -> datetime:
    """A ``State.StartedAt`` from docker (RFC 3339, nanoseconds) as a datetime."""
    seconds, _, fraction = stamp.strip().removesuffix("Z").partition(".")
    return datetime.fromisoformat(f"{seconds}.{fraction[:6].ljust(6, '0')}+00:00")


async def pod_runs_proxy(name: str, proxy: ProxySettings) -> bool:
    """Whether the pod runs, Android inside its gateway's CURRENT namespace, on ``proxy``.

    A gateway started again on its own makes a new namespace while Android stays in
    the old one, cut off: Android must have started after the gateway did.
    """
    android, gateway = await _find(name)
    if android is None or gateway is None or not (android.running and gateway.running):
        return False
    stamps = await docker("inspect", "--format", "{{.State.StartedAt}}", gateway.name, name)
    gateway_started, android_started = (_started_at(s) for s in stamps.split())
    if android_started <= gateway_started:
        return False
    return await runs_proxy(gateway.name, proxy_file(proxy))


async def stop_pod(name: str) -> None:
    """Close the pod's window and stop its running containers, Android first.

    A failed Android stop does not leave the gateway running: both are tried, and
    the first failure is raised after.
    """
    android, gateway = await _find(name)
    await close_viewer(name)
    failure = None
    for container in (android, gateway):
        if container is not None and _up(container):
            failed = await _stop_each([container])
            failure = failure or failed
    if failure is not None:
        raise failure


async def pause_pod(name: str) -> bool:
    """Freeze the pod's running Android, its gateway left up; ``False`` when none runs."""
    android, _gateway = await _find(name)
    if android is None or not android.running:
        return False
    await close_viewer(name)
    await docker("pause", name)
    return True


async def unpause_pod(name: str) -> None:
    """Thaw the pod's paused Android."""
    await docker("unpause", name)


async def stop_paused_pod(name: str) -> bool:
    """Stop the pod only while its Android is still paused; ``False`` when it is not.

    A parked pod that someone opened since (thawed it) is theirs now and left alone.
    """
    android, gateway = await _find(name)
    if android is None or not android.paused:
        return False
    failure = await _stop_each([c for c in (android, gateway) if c is not None and _up(c)])
    if failure is not None:
        raise failure
    return True


async def stop_running_pods() -> int:
    """Stop every running or paused container this app made, Androids first; how many.

    For containers no task of this process owns: left by a crash or a restart, or
    still up when the app shuts down. Each one is tried; the first failure is raised
    after all of them were.
    """
    failure = None
    stopped = 0
    for kind in await list_pods():
        up = [i for i in kind if _up(i)]
        for container in up:
            await close_viewer(container.name)
        failed = await _stop_each(up)
        failure = failure or failed
        stopped += len(up)
    if failure is not None:
        raise failure
    return stopped


async def remove_pod(name: str) -> bool:
    """Delete the pod's containers; ``False`` when it has none.

    A host without Docker has no pod to remove, so a missing docker CLI is "none",
    not an error: deleting an account must not depend on the emulator.
    """
    try:
        android, gateway = await _find(name)
    except EmulatorError as exc:
        if exc.code == _EMULATOR_NOT_FOUND:
            return False
        raise
    await close_viewer(name)
    for container in (android, gateway):
        if container is not None:
            await docker("rm", "--force", container.name)
    return android is not None or gateway is not None
