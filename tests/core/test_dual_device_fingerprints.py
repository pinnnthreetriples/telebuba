"""Tests for dual device fingerprints (Desktop + Mobile), migration #74, and account queries."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from core.db import (
    _get_engine,
    add_account,
    configure_database,
    delete_account,
    get_account,
    get_device_fingerprint,
    list_accounts,
    list_device_fingerprints,
    upsert_device_fingerprint,
)
from core.device_fingerprint import (
    _ANDROID_PROFILES,
    _IOS_PROFILES,
    generate_fingerprint,
    language_pair,
)
from core.migration_steps_dual_device import migrate_dual_device_fingerprints
from core.repositories._device_fingerprint_language import (
    _correct_fingerprint_language,
)
from core.repositories.account_folders._filtered import (
    count_filtered_accounts,
    list_filtered_accounts,
)
from core.repositories.device_fingerprint import list_account_device_fingerprints
from schemas.account_folders import AccountListFilters
from schemas.accounts import AccountCreate
from schemas.device_fingerprint import DeviceFingerprint
from services.accounts.lifecycle import add_account as create_account

if TYPE_CHECKING:
    from pathlib import Path


def test_generate_desktop_and_mobile_fingerprints_share_phone_language() -> None:
    phone = "+79991234567"
    expected_lang, expected_sys_lang = language_pair(phone)

    desktop_fp = generate_fingerprint(
        phone,
        device_type="desktop",
        rng=random.Random(42),  # noqa: S311
    )
    mobile_fp = generate_fingerprint(
        phone,
        device_type="mobile",
        rng=random.Random(42),  # noqa: S311
    )

    assert desktop_fp.device_type == "desktop"
    assert desktop_fp.platform in {"windows", "macos", "linux"}
    assert desktop_fp.lang_code == expected_lang
    assert desktop_fp.system_lang_code == expected_sys_lang

    assert mobile_fp.device_type == "mobile"
    assert mobile_fp.platform in {"android", "ios"}
    assert mobile_fp.lang_code == expected_lang
    assert mobile_fp.system_lang_code == expected_sys_lang


def test_generate_mobile_fingerprint_samples_android_and_ios_pools() -> None:
    android_models = {p[0] for p in _ANDROID_PROFILES}
    ios_models = {p[0] for p in _IOS_PROFILES}
    assert "Samsung Galaxy S24 Ultra" in android_models
    assert "Google Pixel 8" in android_models
    assert "iPhone 15 Pro" in ios_models

    platforms_seen: set[str] = set()
    for seed in range(50):
        fp = generate_fingerprint(
            "+4915123456789",
            device_type="mobile",
            rng=random.Random(seed),  # noqa: S311
        )
        platforms_seen.add(fp.platform)
        assert fp.device_type == "mobile"
        if fp.platform == "android":
            assert fp.device_model in android_models
            assert "SDK 34" in fp.system_version
            assert "Telegram Android" in fp.app_version
        else:
            assert fp.platform == "ios"
            assert fp.device_model in ios_models
            assert fp.system_version in {"17.5", "18.0"}
            assert "Telegram iOS" in fp.app_version

    assert platforms_seen == {"android", "ios"}


def test_migration_74_upgrades_legacy_single_pk_table_and_is_idempotent(tmp_path: Path) -> None:
    configure_database(tmp_path / "legacy_mig74.db")
    engine = _get_engine()

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS device_fingerprints"))
        conn.execute(
            text(
                """
                CREATE TABLE device_fingerprints (
                    account_id TEXT NOT NULL PRIMARY KEY,
                    platform TEXT NOT NULL,
                    device_model TEXT NOT NULL,
                    system_version TEXT NOT NULL,
                    app_version TEXT NOT NULL,
                    lang_code TEXT NOT NULL,
                    system_lang_code TEXT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO device_fingerprints (
                    account_id, platform, device_model, system_version,
                    app_version, lang_code, system_lang_code
                ) VALUES (
                    'acc-legacy', 'windows', 'PC 64bit', 'Windows 10',
                    '4.16.8 x64', 'ru', 'ru-RU'
                )
                """
            )
        )
        migrate_dual_device_fingerprints(conn)
        # Running a second time must be idempotent.
        migrate_dual_device_fingerprints(conn)

        rows = conn.execute(
            text(
                "SELECT account_id, device_type, platform FROM device_fingerprints "
                "WHERE account_id = 'acc-legacy'"
            )
        ).fetchall()
        assert rows == [("acc-legacy", "desktop", "windows")]

        # Now insert a mobile row for the same account_id to verify composite PK.
        conn.execute(
            text(
                """
                INSERT INTO device_fingerprints (
                    account_id, device_type, platform, device_model, system_version,
                    app_version, lang_code, system_lang_code
                ) VALUES (
                    'acc-legacy', 'mobile', 'android', 'Google Pixel 8', 'SDK 34 (Android 14)',
                    'Telegram Android 10.9.2', 'ru', 'ru-RU'
                )
                """
            )
        )
        count = conn.execute(
            text("SELECT COUNT(*) FROM device_fingerprints WHERE account_id = 'acc-legacy'")
        ).scalar_one()
        assert count == 2


@pytest.mark.asyncio
async def test_dual_fingerprints_crud_and_no_row_duplication_on_joins() -> None:
    await add_account(AccountCreate(account_id="acc-dual", phone="+79990001122"))

    desktop_fp = DeviceFingerprint(
        account_id="acc-dual",
        device_type="desktop",
        platform="windows",
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.2.3 x64",
        lang_code="en",
        system_lang_code="en-US",
    )
    mobile_fp = DeviceFingerprint(
        account_id="acc-dual",
        device_type="mobile",
        platform="android",
        device_model="Samsung Galaxy S24 Ultra",
        system_version="SDK 34 (Android 14)",
        app_version="Telegram Android 10.9.2",
        lang_code="en",
        system_lang_code="en-US",
    )
    await upsert_device_fingerprint(desktop_fp)
    await upsert_device_fingerprint(mobile_fp)

    got_desktop = await get_device_fingerprint("acc-dual", device_type="desktop")
    got_mobile = await get_device_fingerprint("acc-dual", device_type="mobile")
    assert got_desktop is not None
    assert got_desktop.device_model == "PC 64bit"
    assert got_mobile is not None
    assert got_mobile.device_model == "Samsung Galaxy S24 Ultra"

    both = await list_account_device_fingerprints("acc-dual")
    assert {fp.device_type for fp in both} == {"desktop", "mobile"}

    # Language backfill when phone is learned updates BOTH desktop and mobile rows.
    with _get_engine().begin() as conn:
        _correct_fingerprint_language(conn, "acc-dual", "+79990001122")
    got_desktop_after = await get_device_fingerprint("acc-dual", device_type="desktop")
    got_mobile_after = await get_device_fingerprint("acc-dual", device_type="mobile")
    assert got_desktop_after is not None
    assert got_desktop_after.system_lang_code == "ru-RU"
    assert got_mobile_after is not None
    assert got_mobile_after.system_lang_code == "ru-RU"

    # list_accounts / get_account / list_filtered_accounts / count_filtered_accounts
    # must NEVER duplicate the account row when both fingerprints exist.
    all_accounts = await list_accounts()
    assert len(all_accounts.accounts) == 1
    acc = all_accounts.accounts[0]
    assert acc.has_desktop_profile is True
    assert acc.has_mobile_profile is True
    assert acc.desktop_fingerprint is not None
    assert acc.desktop_fingerprint.device_model == "PC 64bit"
    assert acc.mobile_fingerprint is not None
    assert acc.mobile_fingerprint.device_model == "Samsung Galaxy S24 Ultra"

    filtered = await list_filtered_accounts(AccountListFilters())
    assert len(filtered.accounts) == 1
    assert await count_filtered_accounts(AccountListFilters()) == 1

    single = await get_account("acc-dual")
    assert single is not None
    assert single.has_desktop_profile is True
    assert single.has_mobile_profile is True

    fp_map = await list_device_fingerprints()
    assert "acc-dual" in fp_map

    await delete_account("acc-dual")
    assert await get_device_fingerprint("acc-dual", device_type="desktop") is None
    assert await get_device_fingerprint("acc-dual", device_type="mobile") is None


@pytest.mark.asyncio
async def test_create_account_respects_selected_device_profiles() -> None:
    acc_both = await create_account(
        AccountCreate(
            account_id="acc-both",
            phone="+79991112233",
            device_profiles=["desktop", "mobile"],
        )
    )
    assert acc_both.has_desktop_profile is True
    assert acc_both.has_mobile_profile is True
    assert acc_both.desktop_fingerprint is not None
    assert acc_both.mobile_fingerprint is not None

    acc_mobile_only = await create_account(
        AccountCreate(
            account_id="acc-mob",
            phone="+79991112244",
            device_profiles=["mobile"],
        )
    )
    assert acc_mobile_only.has_desktop_profile is False
    assert acc_mobile_only.has_mobile_profile is True
    assert acc_mobile_only.desktop_fingerprint is None
    assert acc_mobile_only.mobile_fingerprint is not None
    assert acc_mobile_only.device_platform in {"android", "ios"}
