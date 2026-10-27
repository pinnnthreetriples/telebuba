"""The container's identity: a real phone's firmware, the account's own serial, its region."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Literal

import pytest

from core.country_zones import ZONE_BY_COUNTRY
from core.device_fingerprint_android import (
    ANDROID_DEVICES,
    android_device_model,
    generate_android_fingerprint,
)
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
    [
        ("UnknownPhone 1", "SDK 34"),
        ("GooglePixel 8", "SDK 33"),
        ("GooglePixel 7", "Android 12"),
        ("Samsung Galaxy S22", "Android 14"),
        ("Google Pixel 8", "Android 13"),
    ],
)
def test_a_fingerprint_no_container_can_present_is_refused(model: str, sdk: str) -> None:
    with pytest.raises(EmulatorError) as refused:
        container_spec(_fingerprint(model, sdk), "DE")

    assert refused.value.code == "emulator_device_unknown"


def test_a_sellers_marketing_names_are_the_catalogue_phone_they_mean() -> None:
    fingerprint = _fingerprint("Samsung Galaxy S23", "Android 14")

    spec = container_spec(fingerprint, "DE")

    assert (spec.manufacturer, spec.model, spec.sdk) == ("samsung", "SM-S911B", 34)
    assert aosp_boot_properties(spec)["ro.build.version.release"] == "14"
    assert (fingerprint.device_model, fingerprint.system_version) == (
        "Samsung Galaxy S23",
        "Android 14",
    ), "what Telegram is told stays the seller's"


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


def test_the_vendor_dlkm_partition_names_the_phone_and_its_user_build() -> None:
    props = aosp_boot_properties(container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE"))

    assert props["ro.product.vendor_dlkm.model"] == "Pixel 7"
    assert props["ro.product.vendor_dlkm.brand"] == "google"
    assert props["ro.vendor_dlkm.build.fingerprint"] == props["ro.build.fingerprint"]
    assert props["ro.vendor_dlkm.build.type"] == "user"
    assert props["ro.vendor_dlkm.build.tags"] == "release-keys"


def test_the_builder_host_date_and_board_are_the_firmwares() -> None:
    props = aosp_boot_properties(container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE"))

    assert props["ro.build.user"] == "android-build"
    assert props["ro.build.host"] == "abfarm-release-rbe-64-2004-0074"
    assert props["ro.product.board"] == "panther"
    for partition in ("", "system.", "system_ext.", "product.", "vendor.", "odm.", "vendor_dlkm."):
        assert props[f"ro.{partition}build.date"] == "Sat Feb 11 17:43:27 UTC 2023", partition
        assert props[f"ro.{partition}build.date.utc"] == "1676137407", partition


# The zones the makers' build servers print their dates in.
_BUILD_ZONES = {"UTC": 0, "CST": 8, "KST": 9}


def test_every_build_date_is_its_utc_moment_near_its_security_patch() -> None:
    for blueprint in load_blueprints():
        for sdk, build in blueprint.builds.items():
            *moment, zone, year = build.build_date.split()
            zone_offset = timezone(timedelta(hours=_BUILD_ZONES[zone]))
            printed = " ".join([*moment, year])
            local = datetime.strptime(printed, "%a %b %d %H:%M:%S %Y").replace(tzinfo=zone_offset)
            utc = datetime.fromtimestamp(build.build_date_utc, UTC)
            assert local == utc, (blueprint.model, sdk)
            patch = datetime.strptime(build.security_patch, "%Y-%m-%d").replace(tzinfo=UTC)
            assert abs(utc - patch) <= timedelta(days=62), (blueprint.model, sdk)


# What Telegram's EmuDetector and EmuInputDevicesDetector (DrKLO/Telegram) look for in
# Build.* and system properties; one hit sends Telegram the "emulator" flag (1024).
_EMULATOR_WORDS = (
    "goldfish", "ranchu", "nox", "vbox86", "android_x86", "generic", "sdk", "emulator",
    "genymotion", "droid4x", "bluestacks", "memuhyperv", "virtualbox", "qemu",
)  # fmt: skip


def test_no_property_of_any_phone_names_an_emulator() -> None:
    for device in ANDROID_DEVICES:
        for sdk in device.sdk_levels:
            fingerprint = _fingerprint(android_device_model(device), f"SDK {sdk}")
            props = aosp_boot_properties(container_spec(fingerprint, "DE"))
            for key, value in props.items():
                assert not [w for w in _EMULATOR_WORDS if w in value.lower()], (key, value)
                assert "qemu" not in key, key


def test_no_blueprint_hardware_names_an_emulator() -> None:
    """``ro.hardware`` goes on the command line, so the property check above misses it."""
    for blueprint in load_blueprints():
        name = blueprint.hardware
        assert not [w for w in (*_EMULATOR_WORDS, "redroid") if w in name], name
