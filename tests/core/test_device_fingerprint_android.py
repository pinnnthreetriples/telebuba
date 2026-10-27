"""Which catalogue phone and SDK a mobile fingerprint names, minted or from a seller's JSON."""

from __future__ import annotations

import pytest

from core.device_fingerprint_android import (
    ANDROID_DEVICES,
    android_device_for,
    android_device_model,
    android_phone_for,
    android_sdk_for,
)


@pytest.mark.parametrize(
    ("device_model", "model"),
    [
        # What Telegram Android itself sends: ``Build.MANUFACTURER + Build.MODEL``.
        ("samsungSM-S911B", "SM-S911B"),
        ("GooglePixel 7", "Pixel 7"),
        # The model code alone, any case and spacing.
        ("SM-S911B", "SM-S911B"),
        ("  sm-s911b ", "SM-S911B"),
        ("samsung SM-S911B", "SM-S911B"),
        ("2211133G", "2211133G"),
        # A seller's marketing names, with or without the brand.
        ("Samsung Galaxy S23", "SM-S911B"),
        ("Galaxy S23", "SM-S911B"),
        ("samsung  galaxy   s23", "SM-S911B"),
        ("Samsung Galaxy S23 Ultra", "SM-S918B"),
        ("Google Pixel 7", "Pixel 7"),
        ("Pixel 8", "Pixel 8"),
        ("Xiaomi 13", "2211133G"),
        ("POCO F5", "23049PCD8G"),
        ("Xiaomi POCO F5", "23049PCD8G"),
        ("Redmi Note 12 Pro 5G", "22101316G"),
        ("Xiaomi Redmi Note 12 Pro 5G", "22101316G"),
        ("OnePlus 11", "CPH2449"),
        ("Samsung Galaxy A54 5G", "SM-A546B"),
    ],
)
def test_a_device_model_names_its_catalogue_phone(device_model: str, model: str) -> None:
    device = android_device_for(device_model)

    assert device is not None
    assert device.model == model


@pytest.mark.parametrize(
    "device_model",
    [
        "",
        "UnknownPhone 1",
        "Samsung Galaxy S22",
        # Never a part of a name: the base model is not its Ultra, nor the other way round.
        "Galaxy S23 Ultr",
        "Samsung Galaxy",
        "Galaxy S2",
        "Pixel",
        "Pixel 7 Pro",
        "Xiaomi 13 Pro",
        "Galaxy A54",
        # A brand that is not the phone's.
        "Google Galaxy S23",
        "Samsung Pixel 7",
        "Redmi 13",
    ],
)
def test_a_phone_outside_the_catalogue_is_none(device_model: str) -> None:
    assert android_device_for(device_model) is None


def test_every_catalogue_phone_is_found_by_its_own_device_model_and_sales_name() -> None:
    for device in ANDROID_DEVICES:
        assert android_device_for(android_device_model(device)) == device
        assert android_device_for(device.name) == device


def test_a_name_two_catalogue_phones_share_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    twin = ANDROID_DEVICES[0]._replace(model="SM-S911U")
    monkeypatch.setattr("core.device_fingerprint_android.ANDROID_DEVICES", (*ANDROID_DEVICES, twin))

    assert android_device_for("Samsung Galaxy S23") is None
    assert android_device_for("samsungSM-S911U") == twin


@pytest.mark.parametrize(
    ("system_version", "sdk"),
    [
        ("SDK 34", 34),
        ("SDK 33", 33),
        ("sdk  33", 33),
        ("Android 14", 34),
        ("Android 13", 33),
        ("android 14.0", 34),
        ("Android 13.1", 33),
        (" Android  14 ", 34),
    ],
)
def test_a_system_version_names_its_sdk(system_version: str, sdk: int) -> None:
    assert android_sdk_for(system_version) == sdk


@pytest.mark.parametrize(
    "system_version",
    ["", "34", "SDK", "SDK x", "SDK 34.1", "Android", "Android 99", "Android 14a", "iOS 17"],
)
def test_a_system_version_with_no_known_sdk_is_none(system_version: str) -> None:
    assert android_sdk_for(system_version) is None


def test_a_sellers_galaxy_s23_on_android_14_is_the_catalogue_s23_on_sdk_34() -> None:
    phone = android_phone_for("Samsung Galaxy S23", "Android 14")

    assert phone is not None
    assert (phone.device.manufacturer, phone.device.model, phone.sdk) == (
        "samsung",
        "SM-S911B",
        34,
    )


@pytest.mark.parametrize(
    ("device_model", "system_version"),
    [
        ("Google Pixel 8", "Android 13"),  # Pixel 8 shipped with 14
        ("POCO F5", "SDK 34"),
        ("Samsung Galaxy S23", "Android 12"),
        ("Samsung Galaxy S22", "Android 14"),
    ],
)
def test_a_phone_on_a_release_it_never_ran_is_none(device_model: str, system_version: str) -> None:
    assert android_phone_for(device_model, system_version) is None
