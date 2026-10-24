"""The hardware features the phone declares: a phone's set, not a redroid box's.

``PackageManager.hasSystemFeature`` answers from the XML files of
``/vendor/etc/permissions`` (read by SystemConfig at every boot). redroid's declare
``android.hardware.ethernet``, a single-touch screen and no telephony; every phone of
the catalogue has a GSM radio, a multi-touch screen and USB host and accessory. These
files are AOSP's own (``frameworks/native/data/etc``, the same in 13 and 14) by name
and features, copied over the image's before the boot that reads them.

``android.hardware.ethernet.xml`` is emptied, which drops its feature. eth0 stays up:
the Ethernet service starts for ``android.hardware.usb.host`` as well. Left out on
purpose: NFC and fingerprint (the image has no HAL behind them, so the feature would
come with an adapter that is missing), and sensors (no sensors HAL either; the image's
``handheld_core_hardware.xml`` keeps its accelerometer and compass).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

PERMISSIONS_DIR = "/vendor/etc/permissions"
_TOUCH = "android.hardware.touchscreen"
_FEATURES: dict[str, tuple[str, ...]] = {
    "android.hardware.telephony.gsm.xml": (
        "android.hardware.telephony",
        "android.software.telecom",
        "android.hardware.telephony.radio.access",
        "android.hardware.telephony.subscription",
        "android.hardware.telephony.gsm",
        "android.hardware.telephony.calling",
        "android.hardware.telephony.data",
        "android.hardware.telephony.messaging",
    ),
    "android.hardware.touchscreen.multitouch.jazzhand.xml": (
        _TOUCH,
        f"{_TOUCH}.multitouch",
        f"{_TOUCH}.multitouch.distinct",
        f"{_TOUCH}.multitouch.jazzhand",
        "android.hardware.faketouch",
    ),
    "android.hardware.usb.host.xml": ("android.hardware.usb.host",),
    "android.hardware.usb.accessory.xml": ("android.hardware.usb.accessory",),
    "android.hardware.ethernet.xml": (),
}


def permission_files() -> dict[str, str]:
    """File name -> content of each file that goes into ``PERMISSIONS_DIR``."""
    return {
        name: '<?xml version="1.0" encoding="utf-8"?>\n<permissions>\n'
        + "".join(f'    <feature name="{feature}" />\n' for feature in features)
        + "</permissions>\n"
        for name, features in _FEATURES.items()
    }


def write_permission_files(directory: Path) -> None:
    """Write ``permission_files`` into ``directory``, for one ``docker cp`` of all of them."""
    for name, text in permission_files().items():
        (directory / name).write_text(text, encoding="utf-8", newline="\n")
