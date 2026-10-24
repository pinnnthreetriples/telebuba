"""The hardware features the phone declares: AOSP's own files, a phone's set."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from core.emulator import _features

if TYPE_CHECKING:
    from pathlib import Path

# What Telegram's EmuDetector and EmuInputDevicesDetector look for (DrKLO/Telegram).
_EMU_MARKERS = (
    "goldfish",
    "ranchu",
    "qemu",
    "generic",
    "sdk",
    "nox",
    "vbox",
    "andy",
    "geny",
    "bluestacks",
    "memuhyperv",
    "virtualbox",
    "x86",
    "emulator",
    "droid4x",
)


def _declared() -> dict[str, list[str]]:
    return {
        name: [
            feature.attrib["name"]
            for feature in ET.fromstring(text).iter("feature")  # noqa: S314 - constant text
        ]
        for name, text in _features.permission_files().items()
    }


def test_the_phone_has_a_gsm_radio_a_multitouch_screen_and_usb() -> None:
    declared = _declared()

    assert declared["android.hardware.telephony.gsm.xml"] == [
        "android.hardware.telephony",
        "android.software.telecom",
        "android.hardware.telephony.radio.access",
        "android.hardware.telephony.subscription",
        "android.hardware.telephony.gsm",
        "android.hardware.telephony.calling",
        "android.hardware.telephony.data",
        "android.hardware.telephony.messaging",
    ], "AOSP's GSM file as it is"
    assert (
        "android.hardware.touchscreen.multitouch.jazzhand"
        in declared["android.hardware.touchscreen.multitouch.jazzhand.xml"]
    )
    assert (
        "android.hardware.faketouch"
        in declared["android.hardware.touchscreen.multitouch.jazzhand.xml"]
    ), "every touchscreen phone has it"
    assert declared["android.hardware.usb.host.xml"] == ["android.hardware.usb.host"]
    assert declared["android.hardware.usb.accessory.xml"] == ["android.hardware.usb.accessory"]


def test_ethernet_is_no_longer_declared() -> None:
    assert _declared()["android.hardware.ethernet.xml"] == [], "the image's own file, emptied"


def test_nothing_declared_names_an_emulator() -> None:
    for name, text in _features.permission_files().items():
        lowered = (name + text).lower()
        assert not [marker for marker in _EMU_MARKERS if marker in lowered], name


def test_the_files_are_written_into_a_directory(tmp_path: Path) -> None:
    directory = tmp_path / "features"
    directory.mkdir()

    _features.write_permission_files(directory)

    written = {path.name: path.read_text(encoding="utf-8") for path in directory.iterdir()}
    assert written == _features.permission_files()
