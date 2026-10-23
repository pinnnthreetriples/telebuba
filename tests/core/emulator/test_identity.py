"""The container's identity: a real phone's firmware, the account's own serial, its region."""

from __future__ import annotations

from typing import Literal

import pytest

from core.country_zones import ZONE_BY_COUNTRY
from core.device_fingerprint_android import ANDROID_DEVICES, generate_android_fingerprint
from core.emulator import _profile
from core.emulator._blueprints import find_build, load_blueprints
from core.emulator._errors import EmulatorError
from core.emulator._geo import load_carriers, region_for
from core.emulator._profile import (
    aosp_boot_properties,
    build_fingerprint,
    container_spec,
    derive_serial,
)
from core.web_login._countries import COUNTRY
from tests.core.emulator.test_topology import CHIP


def _fingerprint(model: str, sdk: str, *, account_id: str = "acc", lang: str = "de-DE"):
    return generate_android_fingerprint(account_id, None).model_copy(
        update={"device_model": model, "system_version": sdk, "system_lang_code": lang},
    )


def test_every_catalogue_phone_and_sdk_has_a_real_build() -> None:
    for device in ANDROID_DEVICES:
        for sdk in device.sdk_levels:
            assert find_build(device.manufacturer, device.model, sdk), (device, sdk)


def test_every_blueprint_is_a_catalogue_phone_with_a_build_of_its_own_release() -> None:
    catalogue = {(d.manufacturer, d.model): d.sdk_levels for d in ANDROID_DEVICES}
    for blueprint in load_blueprints():
        assert (blueprint.manufacturer, blueprint.model) in catalogue
        releases = {sdk: build.release for sdk, build in blueprint.builds.items()}
        assert all(release == str(sdk - 20) for sdk, release in releases.items()), releases


def test_the_container_is_the_phone_its_firmware_and_its_region() -> None:
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")

    assert (spec.manufacturer, spec.model, spec.sdk) == ("Google", "Pixel 7", 33)
    props = aosp_boot_properties(spec)
    fingerprint = props["ro.build.fingerprint"]
    assert fingerprint.startswith("google/panther/panther:13/")
    assert fingerprint.endswith(":user/release-keys")
    assert props["ro.vendor.build.fingerprint"] == fingerprint
    assert props["ro.product.vendor.model"] == "Pixel 7"
    assert props["ro.build.version.release"] == "13"
    assert props["ro.build.type"] == "user"
    assert props["ro.sf.lcd_density"] == str(spec.screen.dpi)
    assert props["persist.sys.timezone"] == "Europe/Berlin"
    assert props["persist.sys.locale"] == "de-DE"
    assert props["gsm.sim.operator.iso-country"] == "de"
    assert props["gsm.sim.operator.numeric"].startswith("262")
    left_to_the_image = {"ro.build.version.sdk", "ro.hardware", "ro.debuggable", "ro.secure"}
    assert not left_to_the_image & props.keys()
    assert "ro.serialno" not in props, "init sets it from androidboot.serialno first"


def test_the_container_is_the_phones_board_and_keeps_the_images_composer() -> None:
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")

    assert (spec.hardware, spec.board, spec.platform) == ("panther", "panther", "gs201")
    props = aosp_boot_properties(spec)
    assert props["ro.product.board"] == "panther"
    assert props["ro.board.platform"] == "gs201"
    assert props["ro.hardware.hwcomposer"] == "redroid", "found before ro.hardware's name"
    assert "ro.hardware" not in props, "init sets it from androidboot.hardware first"


# What Telegram's EmuDetector looks for in Build.HARDWARE, BOARD and PRODUCT.
_EMULATOR_MARKS = ("goldfish", "ranchu", "vbox86", "android_x86", "nox", "generic", "sdk", "qemu")


def test_no_blueprint_board_names_an_emulator() -> None:
    for blueprint in load_blueprints():
        for name in (blueprint.hardware, blueprint.board, blueprint.platform):
            assert not any(mark in name.lower() for mark in _EMULATOR_MARKS), name
            assert "redroid" not in name, name


def test_the_chip_is_told_by_ro_soc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_profile, "load_socs", lambda: {"chip": CHIP})
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")

    props = aosp_boot_properties(spec._replace(soc="chip"))
    assert props["ro.soc.model"] == "SM0000"
    assert props["ro.soc.manufacturer"] == "QTI"
    assert not any(
        key.startswith("ro.soc.") for key in aosp_boot_properties(spec._replace(soc=None))
    )


def test_the_language_is_the_fingerprints_not_the_proxy_countrys() -> None:
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33", lang="es-MX"), "DE")

    assert spec.region.locale == "es-MX"
    assert spec.region.timezone == "Europe/Berlin"


def test_an_account_keeps_its_phone_and_two_accounts_differ() -> None:
    first = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")
    again = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "PL")
    other = container_spec(_fingerprint("GooglePixel 7", "SDK 33", account_id="acc-2"), "DE")

    assert first.serial == again.serial
    assert first.serial != other.serial


@pytest.mark.parametrize(
    ("model", "sdk"),
    [("UnknownPhone 1", "SDK 34"), ("GooglePixel 8", "SDK 33"), ("GooglePixel 7", "Android 13")],
)
def test_a_fingerprint_no_container_can_present_is_refused(model: str, sdk: str) -> None:
    with pytest.raises(EmulatorError) as refused:
        container_spec(_fingerprint(model, sdk), "DE")

    assert refused.value.code == "emulator_device_unknown"


def test_a_catalogue_phone_without_a_blueprint_build_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("core.emulator._profile.find_build", lambda *_args: None)

    with pytest.raises(EmulatorError) as refused:
        container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")

    assert refused.value.code == "emulator_device_unknown"


@pytest.mark.parametrize(
    ("prefix", "length", "charset", "alphabet"),
    [
        ("R5C", 11, "alnum", "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        ("", 8, "hex", "0123456789abcdef"),
    ],
)
def test_a_derived_serial_has_its_makers_shape(
    prefix: str, length: int, charset: Literal["alnum", "hex"], alphabet: str
) -> None:
    serial = derive_serial(prefix, length, charset, "acc")

    assert serial.startswith(prefix)
    assert len(serial) == length
    assert set(serial[len(prefix) :]) <= set(alphabet)


def test_a_build_with_its_own_description_and_display_id_keeps_them() -> None:
    spec = container_spec(_fingerprint("OnePlusCPH2449", "SDK 33"), "DE")
    props = aosp_boot_properties(spec)

    assert props["ro.build.description"].startswith("qssi-user 13 ")
    assert props["ro.build.display.id"] == "CPH2449_13.1.0.580(EX01)"
    assert props["ro.build.fingerprint"].startswith("OnePlus/CPH2449EEA/OP594DL1:13/")


def test_a_country_without_carriers_or_zone_is_utc_without_a_sim() -> None:
    region = region_for("AQ", "en-US", seed="acc")

    assert (region.country, region.timezone, region.carrier) == ("aq", "Etc/UTC", None)
    assert region_for(None, "en-US", seed="acc").country is None


def test_an_account_keeps_its_carrier_in_one_country() -> None:
    picks = {region_for("DE", "de-DE", seed="acc").carrier for _ in range(5)}

    assert len(picks) == 1


def test_a_container_without_a_carrier_writes_no_sim_properties() -> None:
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), None)

    assert not [key for key in aosp_boot_properties(spec) if key.startswith("gsm.")]
    assert aosp_boot_properties(spec)["persist.sys.timezone"] == "Etc/UTC"


def test_every_carrier_country_has_a_zone() -> None:
    assert set(load_carriers()) <= set(ZONE_BY_COUNTRY)


def test_the_web_window_keeps_the_shared_zone_table() -> None:
    assert {country: zone for country, (zone, _locale) in COUNTRY.items()} == ZONE_BY_COUNTRY


def test_the_fingerprint_is_put_together_from_the_build() -> None:
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")
    build = spec.build

    assert build_fingerprint(spec) == (
        f"google/panther/panther:13/{build.build_id}/{build.incremental}:user/release-keys"
    )
