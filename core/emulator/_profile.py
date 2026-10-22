"""The container's identity, taken from the account's mobile fingerprint and proxy country.

The container must BE the phone the mobile fingerprint describes: the same
``Build.MANUFACTURER`` and ``Build.MODEL``, the same Android SDK, and that phone's real
firmware for the SDK (``core.emulator._blueprints``), so every ``ro.product.*`` and
``ro.build.*`` an app can read names one real device. Its timezone and carrier follow
the proxy country, its language the fingerprint (``core.emulator._geo``).

The serial number and the IMEI are derived from the account id, so the account keeps
its phone when its container is made again. The IMEI is a test identifier: 15 digits
whose last is the Luhn check digit of the other 14 (kept with the spec; redroid has no
telephony to give it to).
"""

from __future__ import annotations

import hashlib
import random
import shlex
import string
import uuid
from typing import TYPE_CHECKING, Literal, NamedTuple

from core.device_fingerprint_android import ANDROID_DEVICES, android_device_model
from core.emulator._blueprints import Camera, OemBuild, Screen, find_build, load_socs
from core.emulator._errors import EmulatorError
from core.emulator._geo import Region, region_for

if TYPE_CHECKING:
    from collections.abc import Mapping

    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_DEVICE_UNKNOWN: EmulatorRefusalCode = "emulator_device_unknown"

_SERIAL_ALPHABETS = {"alnum": string.digits + string.ascii_uppercase, "hex": "0123456789abcdef"}
# "35" is a GSMA reporting-body prefix; the rest of the body is derived.
# Every partition a ``ro.product.<partition>.*`` / ``ro.<partition>.build.*`` exists for.
_PARTITIONS = ("", "system.", "system_ext.", "product.", "vendor.", "odm.")


class ContainerSpec(NamedTuple):
    manufacturer: str
    model: str
    sdk: int
    brand: str
    product: str
    device: str
    serial: str
    screen: Screen
    camera: Camera
    build: OemBuild
    region: Region
    # The phone's chip (a ``configs/soc`` stem); ``None``: the host's CPUs show through.
    soc: str | None = None


def _derived(seed: str, purpose: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{purpose}:{seed}".encode()).digest(), "big")


def derive_serial(prefix: str, length: int, charset: Literal["alnum", "hex"], seed: str) -> str:
    """``ro.serialno``: ``prefix`` then characters of ``charset``, stable for ``seed``."""
    alphabet = _SERIAL_ALPHABETS[charset]
    number = _derived(seed, "serial")
    tail = ""
    while len(prefix) + len(tail) < length:
        number, digit = divmod(number, len(alphabet))
        tail += alphabet[digit]
    return prefix + tail


def container_spec(fingerprint: DeviceFingerprint, country: str | None) -> ContainerSpec:
    """The container for this mobile fingerprint, or ``emulator_device_unknown``.

    ``country`` is the account's proxy country (ISO-3166 alpha-2), ``None`` when unknown.
    """
    device = next(
        (d for d in ANDROID_DEVICES if android_device_model(d) == fingerprint.device_model),
        None,
    )
    sdk = fingerprint.system_version.removeprefix("SDK ")
    if device is None or not sdk.isdigit() or int(sdk) not in device.sdk_levels:
        raise EmulatorError(_EMULATOR_DEVICE_UNKNOWN)
    found = find_build(device.manufacturer, device.model, int(sdk))
    if found is None:
        raise EmulatorError(_EMULATOR_DEVICE_UNKNOWN)
    blueprint, build = found
    seed = fingerprint.account_id
    return ContainerSpec(
        manufacturer=blueprint.manufacturer,
        model=blueprint.model,
        sdk=int(sdk),
        brand=blueprint.brand,
        product=blueprint.product,
        device=blueprint.device,
        serial=derive_serial(
            blueprint.serial_prefix, blueprint.serial_length, blueprint.serial_charset, seed
        ),
        screen=blueprint.screen,
        camera=blueprint.camera,
        build=build,
        region=region_for(country, fingerprint.system_lang_code, seed=seed),
        soc=blueprint.soc,
    )


def build_fingerprint(spec: ContainerSpec) -> str:
    build = spec.build
    return (
        f"{spec.brand}/{spec.product}/{spec.device}:{build.release}/"
        f"{build.build_id}/{build.incremental}:user/release-keys"
    )


def aosp_boot_properties(spec: ContainerSpec) -> dict[str, str]:
    """The system properties that make Android report ``spec``'s phone, firmware and region.

    Left out on purpose: ``ro.build.version.sdk`` (the image's own, which the SDK check
    reads), ``ro.hardware`` / ``ro.board.platform`` (they pick the HALs the image boots
    with; the chip is told by ``ro.soc.*`` instead), the security flags
    (``ro.debuggable``, ``ro.secure``: adbd depends on them) and ``ro.serialno`` (init
    sets it from ``androidboot.serialno`` before any ``build.prop`` is read, so it goes
    on the command line).
    """
    build = spec.build
    fingerprint = build_fingerprint(spec)
    props: dict[str, str] = {}
    for partition in _PARTITIONS:
        props[f"ro.product.{partition}brand"] = spec.brand
        props[f"ro.product.{partition}manufacturer"] = spec.manufacturer
        props[f"ro.product.{partition}model"] = spec.model
        props[f"ro.product.{partition}name"] = spec.product
        props[f"ro.product.{partition}device"] = spec.device
        props[f"ro.{partition}build.fingerprint"] = fingerprint
        props[f"ro.{partition}build.id"] = build.build_id
        props[f"ro.{partition}build.version.incremental"] = build.incremental
        props[f"ro.{partition}build.version.release"] = build.release
        props[f"ro.{partition}build.tags"] = "release-keys"
        props[f"ro.{partition}build.type"] = "user"
    props |= {
        "ro.bootimage.build.fingerprint": fingerprint,
        "ro.build.display.id": build.display_id or build.build_id,
        "ro.build.version.release_or_codename": build.release,
        "ro.build.version.security_patch": build.security_patch,
        "ro.vendor.build.security_patch": build.security_patch,
        "ro.build.description": build.description
        or f"{spec.product}-user {build.release} {build.build_id} {build.incremental} release-keys",
        "ro.build.flavor": f"{spec.product}-user",
        "ro.build.product": spec.device,
        "ro.sf.lcd_density": str(spec.screen.dpi),
        "persist.sys.timezone": spec.region.timezone,
        "persist.sys.locale": spec.region.locale,
    }
    if spec.soc is not None:
        soc = load_socs()[spec.soc]
        props["ro.soc.manufacturer"] = soc.manufacturer
        props["ro.soc.model"] = soc.model
    carrier = spec.region.carrier
    if carrier is not None and spec.region.country is not None:
        for side in ("gsm.sim.operator", "gsm.operator"):
            props[f"{side}.numeric"] = carrier.numeric
            props[f"{side}.alpha"] = carrier.alpha
            props[f"{side}.iso-country"] = spec.region.country
    return props


REDROID_BOOT_PROP_KEYS: tuple[str, ...] = (
    "ro.boot.redroid_width",
    "ro.boot.redroid_height",
    "ro.boot.redroid_dpi",
    "ro.boot.redroid_fps",
    "ro.boot.redroid_gpu_mode",
    "ro.boot.redroid_overlayfs",
    "ro.boot.use_memfd",
    "ro.boot.use_redroid_overlayfs",
)

USER_BUILD_HARDENING_PROPS: dict[str, str] = {
    "ro.build.type": "user",
    "ro.build.tags": "release-keys",
    "ro.debuggable": "0",
    "ro.secure": "1",
    "ro.boot.verifiedbootstate": "green",
    "ro.boot.flash.locked": "1",
    "ro.boot.vbmeta.device_state": "locked",
}

USB_STEALTH_PROPS: dict[str, str] = {
    "sys.usb.state": "mtp",
    "sys.usb.config": "mtp",
}


def generate_instance_boot_id() -> str:
    """A unique UUIDv4 string for /proc/sys/kernel/random/boot_id."""
    return str(uuid.uuid4())


def build_container_isolation_commands(boot_id: str | None = None) -> list[str]:
    """Commands removing /.dockerenv and bind-mounting a unique boot_id."""
    resolved_boot_id = boot_id or generate_instance_boot_id()
    quoted_boot_id = shlex.quote(resolved_boot_id)
    return [
        "{ umount -l /.dockerenv 2>/dev/null || true; }",
        "{ rm -f /.dockerenv 2>/dev/null || true; }",
        f"printf '%s\\n' {quoted_boot_id} > /dev/.boot_id",
        "chmod 0444 /dev/.boot_id",
        "{ umount -l /proc/sys/kernel/random/boot_id 2>/dev/null || true; }",
        "mount --bind -o ro /dev/.boot_id /proc/sys/kernel/random/boot_id",
    ]


def build_redroid_cleanup_script() -> str:
    """Shell command deleting all dynamic ro.boot.redroid_* properties via getprop + resetprop."""
    return (
        "for prop in $(getprop | sed -n 's/^\\[\\(ro\\.boot\\.redroid_[^]]*\\)\\]:.*/\\1/p'); do "
        '{ resetprop --delete "$prop" 2>/dev/null || true; }; '
        "done"
    )


def build_user_build_resetprop_commands(
    extra_props: Mapping[str, str] | None = None,
) -> list[str]:
    """Commands setting container user-build properties using resetprop -n."""
    commands: list[str] = [build_redroid_cleanup_script()]
    commands.extend(
        f"{{ resetprop --delete {shlex.quote(key)} 2>/dev/null || true; }}"
        for key in REDROID_BOOT_PROP_KEYS
    )

    merged_props = dict(USER_BUILD_HARDENING_PROPS)
    if extra_props:
        merged_props.update(extra_props)

    for key, value in merged_props.items():
        commands.append(f"resetprop -n {shlex.quote(key)} {shlex.quote(str(value))}")

    return commands


def build_usb_stealth_commands() -> list[str]:
    """Commands masking USB/ADB status without triggering adbd shutdown."""
    commands: list[str] = [
        "resetprop -n service.adb.tcp.port 5555",
        "settings put global adb_enabled 0",
        "settings put global development_settings_enabled 0",
    ]
    for key, value in USB_STEALTH_PROPS.items():
        commands.append(f"resetprop -n {shlex.quote(key)} {shlex.quote(value)}")
    return commands


def build_battery_stealth_commands(
    level: int | None = None,
    temp: int | None = None,
) -> list[str]:
    """Commands simulating a realistic mobile battery discharge state."""
    resolved_level = random.randint(35, 90) if level is None else level  # nosec B311  # noqa: S311
    resolved_temp = random.randint(310, 340) if temp is None else temp  # nosec B311  # noqa: S311
    return [
        "dumpsys battery set status 3",
        "dumpsys battery set ac 0",
        "dumpsys battery set usb 0",
        "dumpsys battery set wireless 0",
        f"dumpsys battery set level {resolved_level}",
        f"dumpsys battery set temp {resolved_temp}",
    ]


def build_cellular_stealth_commands() -> list[str]:
    """Commands setting container radio properties to LTE / connected state."""
    return [
        "resetprop -n gsm.network.type LTE",
        "resetprop -n gsm.sim.state READY",
    ]
