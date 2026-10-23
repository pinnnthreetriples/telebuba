"""A mobile account's pod: a net-gateway container and the Android one in its namespace.

The gateway (``core.emulator._gateway``) owns the network: Android is created with
``--network container:<gateway>``, so it has no route of its own, no VPN inside Android
and no ports (the gateway publishes adb at the pod's index). Both containers carry
that index.

``start_pod`` creates what is missing (and remakes a gateway made from an image other
than ``EMULATOR__GATEWAY_IMAGE``, with its Android), gives the gateway the CURRENT
proxy, starts it and waits for its tunnel before Android is started: Android's first
packet meets the tunnel or the kill-switch, never a direct route. ``stop_pod`` stops
Android, then the gateway; ``remove_pod`` deletes both. A failed start undoes what it
did: the containers it created are removed, the ones it found are stopped.
``stop_running_pods`` stops every pod this app left running (a crash, a restart),
Androids before gateways.

Between close visits ``pause_pod`` freezes Android (``docker pause``: no CPU, its memory
kept) and leaves the gateway up; ``unpause_pod`` thaws it in a moment, with no boot.
A paused container is ended with ``docker kill`` right after it is thawed: a few
seconds of grace would let the app reconnect and show the account online.

With shared /data on (``core.emulator._data``) Android's writes go to the pod's diff
directory, which outlives the container: an Android made again after its gateway
went finds the account's sign-in there. Only ``remove_pod`` deletes it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from core.config import settings
from core.emulator._data import data_layers, recorded_diff, remove_diff
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
from core.emulator.host import gpu_mode
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
    from core.emulator._data import DataLayers
    from core.emulator._profile import ContainerSpec
    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxySettings

_EMULATOR_NOT_FOUND: EmulatorRefusalCode = "emulator_not_found"

# Stdlib sink, names and codes only (see ``core.emulator._errors``).
logger = logging.getLogger(__name__)


class StartedPod(NamedTuple):
    """The running Android of a started pod, and whether this start made its /data."""

    instance: Instance
    # An Android on a diff an earlier one left (its sign-in) has none of its own.
    fresh_data: bool


# Picking a free index and creating the gateway that holds it is one step (a stale
# gateway's removal too, so its index is not taken meanwhile); nothing slower goes in it.
_CREATE_LOCK = asyncio.Lock()
# One start per pod at a time: what one start found must not be changed under it by
# another of the same pod. Different pods start side by side.
_POD_LOCKS: dict[str, asyncio.Lock] = {}
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


async def start_pod(name: str, spec: ContainerSpec, proxy: ProxySettings) -> StartedPod:
    """Start the pod whose Android container is ``name``, on ``proxy``: its ``StartedPod``.

    The phone and the proxy are refused before anything is created. Whatever of the
    pod is running is stopped first, so the gateway always starts on ``proxy``. Starts
    of one pod queue behind each other (``_POD_LOCKS``); only the index pick and the
    gateway create are shared with other pods' starts (``_CREATE_LOCK``).
    """
    image_for(spec)
    build_prop(spec)
    proxy_text = proxy_file(proxy)
    address = await resolve_address(proxy.host)
    # A pull or build may take minutes: outside the locks.
    gateway_image = await ensure_pod_images(spec)
    async with _POD_LOCKS.setdefault(name, asyncio.Lock()):
        return await _start(name, spec, proxy_text, address, gateway_image)


async def _start(
    name: str, spec: ContainerSpec, proxy_text: str, address: str, gateway_image: str | None
) -> StartedPod:
    """``start_pod`` once its inputs are checked, holding the pod's own lock.

    ``gateway_image`` is the configured gateway image's ID as ``ensure_pod_images`` read it
    (``None``: just built, not read yet).
    """
    gateway = gateway_name(name)
    found: set[str] = set()
    created: set[str] = set()
    layers = None
    try:
        android, current = await _find(name)
        # A gateway of an image other than the configured one is made again, so a
        # bumped tag reaches the pods made before it.
        stale = current is not None and await _stale_gateway(gateway, gateway_image)
        gone = current is None or stale
        # Only an Android about to be made needs its base: refused before anything is.
        remade = android is None or gone
        # The diff of an Android made again stays its own, whatever is configured now.
        kept = await recorded_diff(name) if android is not None and gone else None
        layers = await data_layers(name, spec.sdk, kept) if remade else None
        # Before an Android is removed: a GPU the host lacks must not leave none.
        gpu = await gpu_mode() if remade else "guest"
        if current is None or stale:
            await ensure_network()  # safe to race: another start may make it first
            async with _CREATE_LOCK:
                # Only once the lock is held: a start cancelled while it waits has removed
                # nothing, so Android's label still names its diff for the next start.
                if android is not None:
                    # Its namespace goes with its gateway: Android is made again in the new one.
                    await docker("rm", "--force", name)
                    android = None
                if stale:
                    await docker("rm", "--force", gateway)
                # A remade gateway keeps its pod's index, so its adb port and cores.
                index = current.index if current is not None else await _free_index()
                current = None
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
        fresh = False
        if android is None:
            created.add(name)  # before: a failed identity copy leaves it half made
            fresh = _own_data(layers)
            await create_instance(
                name, spec, index=index, gateway=gateway, gpu=gpu, data=layers, kept_diff=kept
            )
        else:
            await refresh_identity(name, spec, index)  # the proxy may have moved country
        await launch_instance(name, index)
    except BaseException:
        await asyncio.shield(_undo((name, gateway), found=found, created=created))
        if layers is not None and layers.fresh:
            await asyncio.shield(_forget_diff(name))
        raise
    return StartedPod(Instance(index, name, running=True), fresh_data=fresh)


def _own_data(layers: DataLayers | None) -> bool:
    """Whether a new Android's /data is its own: in the container, or on a diff just made."""
    return layers is None or layers.fresh


async def _stale_gateway(gateway: str, configured: str | None) -> bool:
    """Whether ``gateway`` was made from an image other than the configured one, by image ID.

    ``configured`` is that image's ID when already read, else it is read here.
    """
    made_from = await docker("inspect", "--format", "{{.Image}}", gateway)
    if configured is None:
        image = settings.emulator.gateway_image
        configured = await docker("image", "inspect", "--format", "{{.Id}}", image)
    return made_from.strip() != configured.strip()


async def _forget_diff(name: str) -> None:
    """Remove the diff a failed start made, so no later Android boots on its leftovers."""
    try:
        await remove_diff(name)
    except EmulatorError as exc:
        logger.warning("pod cleanup of %s's data failed (%s)", name, exc.code)


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
    """Delete the pod's /data diff, then its containers; ``False`` when it has no container.

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
    recorded = await recorded_diff(name) if android is not None else None
    if android is not None and _up(android):
        await _halt(android)  # its /data still before the diff goes
    # Before the containers: a diff that could not go stays named by Android's label,
    # so a later removal finds it again.
    await remove_diff(name, recorded)
    for container in (android, gateway):
        if container is not None:
            await docker("rm", "--force", container.name)
    return android is not None or gateway is not None
