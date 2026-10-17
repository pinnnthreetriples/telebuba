"""A mobile account's pod: a net-gateway container and the Android one in its namespace.

The gateway (``core.emulator._gateway``) owns the network: Android is created with
``--network container:<gateway>``, so it has no route of its own, no VPN inside Android
and no ports (the gateway publishes adb at the pod's index). Both containers carry
that index.

``start_pod`` creates what is missing, gives the gateway the CURRENT proxy, starts it
and waits for its tunnel before Android is started: Android's first packet meets the
tunnel or the kill-switch, never a direct route. ``stop_pod`` stops Android, then the
gateway; ``remove_pod`` deletes both. A failed start undoes what it did: the
containers it created are removed, the ones it found are stopped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core.emulator._errors import EmulatorError
from core.emulator._gateway import (
    create_gateway,
    gateway_name,
    give_proxy,
    proxy_file,
    resolve_address,
    runs_proxy,
    wait_for_tunnel,
)
from core.emulator._process import docker
from core.emulator.redroid import (
    ANDROID_LABEL,
    GATEWAY_LABEL,
    Instance,
    build_prop,
    close_viewer,
    create_instance,
    find_instance,
    image_for,
    launch_instance,
    list_containers,
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


async def _find(name: str) -> tuple[Instance | None, Instance | None]:
    """The pod's Android container and its gateway, each ``None`` when missing."""
    gateway = gateway_name(name)
    gateways = await list_containers(GATEWAY_LABEL)
    return await find_instance(name), next((g for g in gateways if g.name == gateway), None)


async def _free_index() -> int:
    labels = (ANDROID_LABEL, GATEWAY_LABEL)
    used = {i.index for label in labels for i in await list_containers(label)}
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
                await create_gateway(gateway, index)
                created.add(gateway)
            else:
                index = current.index
        for container in (android, current):
            if container is not None:
                found.add(container.name)
                if container.running:
                    await docker("stop", container.name)
        await give_proxy(gateway, proxy_text, address)
        await docker("start", gateway)
        await wait_for_tunnel(gateway)
        if android is None:
            created.add(name)  # before: a failed identity copy leaves it half made
            await create_instance(name, spec, index=index, gateway=gateway)
        else:
            await refresh_identity(name, spec)  # the proxy may have moved country
        await launch_instance(index)
    except BaseException:
        await asyncio.shield(_undo((name, gateway), found=found, created=created))
        raise
    return Instance(index, name, running=True)


async def _undo(containers: tuple[str, ...], *, found: set[str], created: set[str]) -> None:
    for container in containers:
        args = ("rm", "--force") if container in created else ("stop",)
        if container in created | found:
            try:
                await docker(*args, container)
            except EmulatorError as exc:
                logger.warning("pod cleanup of %s failed (%s)", container, exc.code)


async def pod_runs_proxy(name: str, proxy: ProxySettings) -> bool:
    """Whether the pod's gateway is running and was started on ``proxy``."""
    _android, gateway = await _find(name)
    if gateway is None or not gateway.running:
        return False
    return await runs_proxy(gateway.name, proxy_file(proxy))


async def stop_pod(name: str) -> None:
    """Close the pod's window and stop its running containers, Android first."""
    android, gateway = await _find(name)
    await close_viewer(name)
    for container in (android, gateway):
        if container is not None and container.running:
            await docker("stop", container.name)


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
