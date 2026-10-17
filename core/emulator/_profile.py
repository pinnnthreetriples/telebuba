"""The container's identity, taken from the account's mobile fingerprint.

The container must BE the phone the mobile fingerprint describes: the same
``Build.MANUFACTURER`` and ``Build.MODEL`` and the same Android SDK. The IMEI is a
test identifier: 15 digits whose last is the Luhn check digit of the other 14 (kept
with the spec; redroid has no telephony to give it to).
"""

from __future__ import annotations

import secrets
import string
from typing import TYPE_CHECKING, NamedTuple

from core.device_fingerprint_android import ANDROID_DEVICES, android_device_model
from core.emulator._errors import EmulatorError

if TYPE_CHECKING:
    from schemas.device_fingerprint import DeviceFingerprint
    from schemas.emulator import EmulatorRefusalCode

_EMULATOR_DEVICE_UNKNOWN: EmulatorRefusalCode = "emulator_device_unknown"

_IMEI_LENGTH = 15
# "35" is a GSMA reporting-body prefix; the rest of the body is random.
_IMEI_PREFIX = "35"


class ContainerSpec(NamedTuple):
    manufacturer: str
    model: str
    sdk: int
    imei: str


def luhn_check_digit(body: str) -> str:
    """The digit that makes ``body + digit`` pass the Luhn check."""
    total = 0
    for position, char in enumerate(reversed(body)):
        digit = int(char)
        if position % 2 == 0:
            digit *= 2
            if digit > 9:  # noqa: PLR2004 - Luhn folds a doubled digit back to one
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def generate_imei() -> str:
    body = _IMEI_PREFIX + "".join(
        secrets.choice(string.digits) for _ in range(_IMEI_LENGTH - len(_IMEI_PREFIX) - 1)
    )
    return body + luhn_check_digit(body)


def is_valid_imei(imei: str) -> bool:
    return len(imei) == _IMEI_LENGTH and imei.isdigit() and luhn_check_digit(imei[:-1]) == imei[-1]


def container_spec(fingerprint: DeviceFingerprint) -> ContainerSpec:
    """The container for this mobile fingerprint, or ``emulator_device_unknown``."""
    device = next(
        (d for d in ANDROID_DEVICES if android_device_model(d) == fingerprint.device_model),
        None,
    )
    sdk = fingerprint.system_version.removeprefix("SDK ")
    if device is None or not sdk.isdigit() or int(sdk) not in device.sdk_levels:
        raise EmulatorError(_EMULATOR_DEVICE_UNKNOWN)
    return ContainerSpec(device.manufacturer, device.model, int(sdk), generate_imei())
