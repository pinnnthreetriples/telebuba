"""The pod's net-gateway container: the network namespace Android joins, and its tunnel.

The image is ``docker/net-gateway`` (``EMULATOR__GATEWAY_IMAGE``). Everything sent in
its namespace leaves through tun0 to the account's SOCKS5 proxy, DNS included (the
tunnel's mapped DNS, so the proxy resolves the names), or not at all: its kill-switch
drops all egress before the tunnel is up and after it dies, and IPv6 is off.

Gateways join their own bridge network (``EMULATOR__GATEWAY_NETWORK``) with
inter-container traffic off: no other container can reach a pod's unauthenticated adb.

The proxy reaches the gateway as a FILE copied into the stopped container from a
scratch directory deleted right after, never in an env var (``docker inspect`` shows
env, and ``docker create -e`` puts it in argv), argv, a log line or an error. What
``docker inspect`` shows of a gateway: image, labels, NET_ADMIN, /dev/net/tun, the IPv6
sysctls and the adb port binding, nothing of the proxy. The file stays inside the
container (``/etc/net-gateway/proxy``; the tunnel config rendered from it lives in a
tmpfs), readable by whoever may ``docker exec`` (root on the Docker host anyway).
The proxy's host is resolved here, on the host, so a hostname works and the gateway
needs no DNS before its tunnel is up.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator._errors import EmulatorError
from core.emulator._process import docker
from core.emulator.redroid import ADB_PORT, GATEWAY_LABEL, INDEX_LABEL

if TYPE_CHECKING:
    from schemas.emulator import EmulatorRefusalCode
    from schemas.proxy import ProxySettings

_EMULATOR_COMMAND_FAILED: EmulatorRefusalCode = "emulator_command_failed"
_EMULATOR_COMMAND_TIMEOUT: EmulatorRefusalCode = "emulator_command_timeout"
_EMULATOR_PROXY_UNSUPPORTED: EmulatorRefusalCode = "emulator_proxy_unsupported"

# ``docker cp`` of this directory lands at /etc/net-gateway, where the entrypoint reads it.
_CONFIG_DIR = "net-gateway"
_PROXY_FILE = f"/etc/{_CONFIG_DIR}/proxy"


def gateway_name(name: str) -> str:
    """The gateway of the pod whose Android container is named ``name``."""
    return f"{name}-net"


def proxy_file(proxy: ProxySettings) -> str:
    """The gateway's ``proxy`` file: host, port, username, password, one per line.

    Only SOCKS5 can be tunnelled, and a value with a line break would shift the lines.
    """
    fields = (proxy.host, str(proxy.port), proxy.username or "", proxy.password or "")
    if proxy.proxy_type != "socks5" or any(c in f for f in fields for c in "\r\n\0"):
        raise EmulatorError(_EMULATOR_PROXY_UNSUPPORTED)
    return "\n".join(fields) + "\n"


async def resolve_address(host: str) -> str:
    """The proxy's IPv4 address; a host with none is refused (IPv6 is off in the pod)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, None, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
    except (OSError, UnicodeError) as exc:
        raise EmulatorError(_EMULATOR_PROXY_UNSUPPORTED) from exc
    return str(infos[0][4][0])


async def ensure_network() -> None:
    """Create the gateways' bridge network when it is missing (another start may race)."""
    network = settings.emulator.gateway_network
    try:
        await docker("network", "inspect", network)
    except EmulatorError as exc:
        if exc.code != _EMULATOR_COMMAND_FAILED:
            raise
    else:
        return
    try:
        await docker(
            "network",
            "create",
            "--driver",
            "bridge",
            "--opt",
            "com.docker.network.bridge.enable_icc=false",
            network,
        )
    except EmulatorError as exc:
        if exc.code != _EMULATOR_COMMAND_FAILED:
            raise
        await docker("network", "inspect", network)  # made by the race's winner, or raises


async def create_gateway(gateway: str, index: int) -> None:
    """A new, stopped gateway: it holds the pod's index and publishes Android's adb.

    ``/run`` is a tmpfs, so the tunnel config rendered there never reaches a layer.
    """
    port = settings.emulator.redroid_first_adb_port + index
    await docker(
        "create",
        "--name",
        gateway,
        "--network",
        settings.emulator.gateway_network,
        "--tmpfs",
        "/run",
        "--label",
        f"{GATEWAY_LABEL}=1",
        "--label",
        f"{INDEX_LABEL}={index}",
        "--cap-add",
        "NET_ADMIN",
        "--device",
        "/dev/net/tun",
        "--sysctl",
        "net.ipv6.conf.all.disable_ipv6=1",
        "--sysctl",
        "net.ipv6.conf.default.disable_ipv6=1",
        "--publish",
        f"127.0.0.1:{port}:{ADB_PORT}",
        settings.emulator.gateway_image,
    )


def _write_private(directory: Path, files: dict[str, str]) -> None:
    directory.mkdir(mode=0o700)
    for file_name, text in files.items():
        descriptor = os.open(directory / file_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)


async def give_proxy(gateway: str, proxy_text: str, address: str) -> None:
    """Copy the proxy into the stopped ``gateway``; the host-side copy is gone on return."""
    with tempfile.TemporaryDirectory(prefix="telebuba-gateway-") as scratch:
        config = Path(scratch) / _CONFIG_DIR
        files = {"proxy": proxy_text, "address": f"{address}\n"}
        await asyncio.to_thread(_write_private, config, files)
        await docker("cp", str(config), f"{gateway}:/etc/")


async def wait_for_tunnel(gateway: str) -> None:
    """Return once the gateway's tunnel carries its namespace; ``emulator_command_timeout`` else.

    A failed probe is polled again: ``docker exec`` right after ``docker start`` can
    exit 255 while the container comes up. A gateway whose entrypoint failed has
    exited, so its probes keep failing until the time runs out.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.emulator.tunnel_timeout_seconds
    while True:
        try:
            await docker("exec", gateway, "net-gateway", "ready")
        except EmulatorError as exc:
            if exc.code != _EMULATOR_COMMAND_FAILED:
                raise
            if loop.time() >= deadline:
                raise EmulatorError(_EMULATOR_COMMAND_TIMEOUT) from exc
            await asyncio.sleep(settings.emulator.poll_seconds)
        else:
            return


async def runs_proxy(gateway: str, proxy_text: str) -> bool:
    """Whether the running ``gateway`` was given ``proxy_text``.

    Compared by digest, so the credentials never leave the container; a gateway that
    cannot answer is not running that proxy.
    """
    try:
        listing = await docker("exec", gateway, "sha256sum", _PROXY_FILE)
    except EmulatorError as exc:
        if exc.code != _EMULATOR_COMMAND_FAILED:
            raise
        return False
    return listing.split(" ", 1)[0] == hashlib.sha256(proxy_text.encode()).hexdigest()
