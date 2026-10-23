"""Unit tests for Stage 4 telemetry, LTE cellular profile, GPS EXIF, and APK versions."""

from __future__ import annotations

import hashlib
import io
import random
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from PIL.ExifTags import GPS, IFD

from core.android_apk import (
    PinnedApkNotFoundError,
    list_local_apk_versions,
    resolve_pinned_apk_path,
    verify_local_apk,
)
from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import adb
from core.emulator._battery import (
    BatteryTelemetryState,
    build_battery_telemetry_commands,
    build_sensor_jitter_commands,
    generate_initial_battery_state,
    step_battery_state,
)
from core.emulator._errors import EmulatorError
from core.emulator._geo import COUNTRY_COORDINATES
from core.emulator._profile import aosp_boot_properties, container_spec
from core.emulator._seed_data import (
    CallLogSeedEntry,
    Contact,
    SmsSeedEntry,
    _decimal_to_dms,
    build_call_and_sms_seed_commands,
    generate_call_and_sms_history,
    make_photos,
    photo_exif,
)
from tests.core._apk_builder import build_apk

if TYPE_CHECKING:
    from pathlib import Path


def _fingerprint(model: str = "GooglePixel 7", sdk: str = "SDK 33", *, account_id: str = "acc"):
    """Build mock fingerprint model for tests."""
    return generate_android_fingerprint(account_id, None).model_copy(
        update={"device_model": model, "system_version": sdk, "system_lang_code": "en-US"},
    )


def test_battery_telemetry_state_and_commands() -> None:
    """Test battery state calculation and dumpsys command formatting."""
    state = generate_initial_battery_state(seed=42)
    assert 38 <= state.level <= 89
    assert 308 <= state.temp_tenths_c <= 342
    assert state.status == 3
    assert state.charge_counter_uah == int(4500 * 1000 * (state.level / 100.0))

    stepped = step_battery_state(state, seed=42)
    assert stepped.level == state.level - 1
    assert 295 <= stepped.temp_tenths_c <= 368
    assert stepped.charge_counter_uah == int(4500 * 1000 * (stepped.level / 100.0))

    cmds = build_battery_telemetry_commands(stepped)
    cmd_text = "\n".join(cmds)
    assert "dumpsys battery set ac 0" in cmd_text
    assert "dumpsys battery set usb 0" in cmd_text
    assert "dumpsys battery set wireless 0" in cmd_text
    assert f"dumpsys battery set status {stepped.status}" in cmd_text
    assert f"dumpsys battery set level {stepped.level}" in cmd_text
    assert f"dumpsys battery set temp {stepped.temp_tenths_c}" in cmd_text
    assert f"dumpsys battery set counter {stepped.charge_counter_uah}" in cmd_text


def test_battery_telemetry_deterministic_for_account_and_date() -> None:
    """Test determinism of initial battery state for same account and date."""
    today = date(2026, 10, 22)
    first = generate_initial_battery_state("acc-123", today)
    again = generate_initial_battery_state("acc-123", today)
    other_account = generate_initial_battery_state("acc-456", today)

    assert first.level == again.level
    assert first.temp_tenths_c == again.temp_tenths_c
    assert first.voltage_mv == again.voltage_mv
    assert first.charge_counter_uah == again.charge_counter_uah
    assert (first.level, first.temp_tenths_c) != (other_account.level, other_account.temp_tenths_c)


def test_battery_min_level_clamping() -> None:
    """Test that battery discharge respects min_level clamping."""
    low = BatteryTelemetryState(level=15, temp_tenths_c=310, voltage_mv=3700)
    stepped = step_battery_state(low, min_level=15)
    assert stepped.level == 15


def test_sensor_jitter_commands() -> None:
    """Test accelerometer and gyroscope sensor jitter commands generation."""
    cmds = build_sensor_jitter_commands(seed=42)
    assert len(cmds) == 2
    assert "setprop debug.sensors.accelerometer" in cmds[0]
    assert "setprop debug.sensors.gyroscope" in cmds[1]


@pytest.mark.asyncio
async def test_adb_battery_telemetry_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test adb battery telemetry application and error propagation."""
    mock_shell = AsyncMock(return_value="")
    monkeypatch.setattr(adb, "shell", mock_shell)

    initial = await adb.apply_battery_telemetry("emulator-5554", seed=10)
    assert 38 <= initial.level <= 89
    assert mock_shell.await_count == 1
    # Check that shell arguments are chained with && for failure propagation
    args = mock_shell.call_args_list[0][0][1:]
    assert "dumpsys" in args
    assert "battery" in args
    assert "&&" in args
    assert ";" not in args

    next_state = await adb.step_battery_discharge("emulator-5554", initial, seed=10)
    assert next_state.level == initial.level - 1
    # 1 from apply_battery_telemetry, 2 from step_battery_discharge (battery && + sensor ;)
    assert mock_shell.await_count == 3


@pytest.mark.asyncio
async def test_adb_battery_telemetry_propagates_battery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that battery command failures propagate as EmulatorError."""
    mock_shell = AsyncMock(side_effect=EmulatorError("emulator_command_failed"))
    monkeypatch.setattr(adb, "shell", mock_shell)

    with pytest.raises(EmulatorError, match="emulator_command_failed"):
        await adb.apply_battery_telemetry("emulator-5554", seed=10)

    initial = BatteryTelemetryState(level=50, temp_tenths_c=320, voltage_mv=3800)
    with pytest.raises(EmulatorError, match="emulator_command_failed"):
        await adb.step_battery_discharge("emulator-5554", initial, seed=10)


@pytest.mark.asyncio
async def test_adb_battery_telemetry_tolerates_sensor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that sensor jitter failures are suppressed and do not fail the battery step."""
    calls: list[list[str]] = []

    async def shell_side_effect(_serial: str, *args: str, **_kwargs: object) -> str:
        calls.append(list(args))
        if any("sensors" in a for a in args):
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return ""

    monkeypatch.setattr(adb, "shell", shell_side_effect)

    initial = BatteryTelemetryState(level=50, temp_tenths_c=320, voltage_mv=3800)
    next_state = await adb.step_battery_discharge("emulator-5554", initial, seed=10)
    assert next_state.level == 49
    assert len(calls) == 2


def test_lte_cellular_properties_in_aosp_boot_properties() -> None:
    """Test LTE network and SIM properties in AOSP boot props."""
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")
    props = aosp_boot_properties(spec)

    assert props["gsm.network.type"] == "LTE"
    assert props["gsm.current.phone-type"] == "1"
    assert props["gsm.sim.state"] == "READY"
    assert props["gsm.sim.operator.iso-country"] == "de"


def test_lte_cellular_properties_absent_without_carrier() -> None:
    """Test absence of cellular props when carrier is None."""
    # A territory without carrier in carriers_mcc_mnc.json
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), None)
    props = aosp_boot_properties(spec)

    assert "gsm.network.type" not in props
    assert "gsm.current.phone-type" not in props
    assert "gsm.sim.state" not in props


def test_country_coordinates_dictionary() -> None:
    """Test centroid coordinates validity across mapped countries."""
    required = {"US", "RU", "DE", "GB", "FR", "BR", "IN", "KZ", "UA", "TR", "PL", "IT", "ES", "NL"}
    assert required.issubset(COUNTRY_COORDINATES.keys())
    for code, (lat, lon) in COUNTRY_COORDINATES.items():
        assert -90.0 <= lat <= 90.0, f"invalid lat for {code}"
        assert -180.0 <= lon <= 180.0, f"invalid lon for {code}"


def test_photo_exif_contains_gps_info_and_centroid_offset() -> None:
    """Test photo EXIF contains GPS IFD with centroid offsets."""
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "DE")
    rng = random.Random(123)  # noqa: S311
    now = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    exif = photo_exif(spec, now, rng)

    gps = exif.get_ifd(IFD.GPSInfo)
    assert GPS.GPSLatitude in gps
    assert GPS.GPSLatitudeRef in gps
    assert GPS.GPSLongitude in gps
    assert GPS.GPSLongitudeRef in gps

    lat_dms = gps[GPS.GPSLatitude]
    lon_dms = gps[GPS.GPSLongitude]
    assert len(lat_dms) == 3
    assert len(lon_dms) == 3

    # Calculate decimal lat and lon
    lat = float(lat_dms[0]) + float(lat_dms[1]) / 60.0 + float(lat_dms[2]) / 3600.0
    lon = float(lon_dms[0]) + float(lon_dms[1]) / 60.0 + float(lon_dms[2]) / 3600.0
    if gps[GPS.GPSLatitudeRef] == "S":
        lat = -lat
    if gps[GPS.GPSLongitudeRef] == "W":
        lon = -lon

    de_lat, de_lon = COUNTRY_COORDINATES["DE"]
    assert abs(lat - de_lat) <= 0.4
    assert abs(lon - de_lon) <= 0.4


def test_photos_written_with_pillow_have_valid_readable_gps() -> None:
    """Test that generated photo bytes contain readable GPS EXIF."""
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "US")
    rng = random.Random(456)  # noqa: S311
    photos = make_photos(spec, rng, datetime(2026, 10, 22, 12, 0, tzinfo=UTC), count=2)
    assert len(photos) == 2

    for photo in photos:
        img = Image.open(io.BytesIO(photo.jpeg))
        exif = img.getexif()
        gps = exif.get_ifd(IFD.GPSInfo)
        assert gps[GPS.GPSLatitudeRef] in ("N", "S")
        assert gps[GPS.GPSLongitudeRef] in ("E", "W")


def test_call_and_sms_history_generation() -> None:
    """Test deterministic generation of call log and SMS entries."""
    contacts = [
        Contact("John", "Doe", "+12025550111", "john@example.com", "Acme"),
        Contact("Jane", "Smith", "+12025550122", None, None),
    ]
    calls, sms_list = generate_call_and_sms_history(
        contacts,
        seed="test-seed",
        locale="ru_RU",
        call_count=5,
        sms_count=3,
        now_ms=1700000000000,
    )
    assert len(calls) == 5
    assert len(sms_list) == 3

    for call in calls:
        assert isinstance(call, CallLogSeedEntry)
        assert call.number in ("+12025550111", "+12025550122")
        assert call.call_type in (1, 2, 3)
        assert call.timestamp_ms < 1700000000000
        if call.call_type == 3:
            assert call.duration_sec == 0
        else:
            assert call.duration_sec >= 18

    for sms in sms_list:
        assert isinstance(sms, SmsSeedEntry)
        assert sms.sender in ("+12025550111", "+12025550122")
        assert sms.timestamp_ms < 1700000000000
        assert sms.read == 1
        # ru locale template check (Cyrillic content)
        assert any("\u0400" <= c <= "\u04ff" for c in sms.body)


def test_call_and_sms_history_deterministic_without_now_ms() -> None:
    """Test that seed without now_ms produces stable deterministic timestamps across retries."""
    contacts = [Contact("John", "Doe", "+12025550111", None, None)]
    calls1, sms1 = generate_call_and_sms_history(contacts, seed="stable-retry-seed")
    calls2, sms2 = generate_call_and_sms_history(contacts, seed="stable-retry-seed")

    assert [c.timestamp_ms for c in calls1] == [c.timestamp_ms for c in calls2]
    assert [s.timestamp_ms for s in sms1] == [s.timestamp_ms for s in sms2]
    assert [c.number for c in calls1] == [c.number for c in calls2]


def test_build_call_and_sms_seed_commands() -> None:
    """Test call and SMS content delete and insert commands."""
    calls = [
        CallLogSeedEntry(
            number="+12025550111", timestamp_ms=1600000000, duration_sec=45, call_type=1
        )
    ]
    sms = [SmsSeedEntry(sender="+12025550222", body="Hello there", timestamp_ms=1600000000, read=1)]

    cmds = build_call_and_sms_seed_commands(calls, sms)
    assert "WRITE_SMS" in cmds[0]
    assert "READ_SMS" in cmds[1]
    assert "RECEIVE_SMS" in cmds[2]
    assert "WRITE_CALL_LOG" in cmds[3]
    assert "READ_CALL_LOG" in cmds[4]
    assert "WRITE_CONTACTS" in cmds[5]
    assert "READ_CONTACTS" in cmds[6]

    # Deduplication before insert
    assert "content delete --uri content://call_log/calls" in cmds[7]
    assert "date IN (1600000000)" in cmds[7]
    assert "content delete --uri content://sms" in cmds[8]
    assert "date IN (1600000000)" in cmds[8]

    assert "content insert --uri content://call_log/calls" in cmds[9]
    assert "--bind number:s:'+12025550111'" in cmds[9]
    assert cmds[9].endswith("2>/dev/null || true")

    assert "content insert --uri content://sms" in cmds[10]
    assert "--bind address:s:'+12025550222'" in cmds[10]
    assert "--bind body:s:'Hello there'" in cmds[10]
    assert cmds[10].endswith("2>/dev/null || true")

    assert cmds[11] == "appops reset com.android.shell 2>/dev/null || true"
    assert cmds[-1] == "appops reset com.android.shell 2>/dev/null || true"
    assert [c for c in cmds if "appops reset" in c] == [
        "appops reset com.android.shell 2>/dev/null || true"
    ]

    empty_cmds = build_call_and_sms_seed_commands([], [])
    assert empty_cmds[-1] == "appops reset com.android.shell 2>/dev/null || true"

    calls_only = build_call_and_sms_seed_commands(calls, [])
    assert calls_only[-1] == "appops reset com.android.shell 2>/dev/null || true"

    sms_only = build_call_and_sms_seed_commands([], sms)
    assert sms_only[-1] == "appops reset com.android.shell 2>/dev/null || true"

    multi_cmds = build_call_and_sms_seed_commands([*calls, *calls], [*sms, *sms])
    assert multi_cmds[-1] == "appops reset com.android.shell 2>/dev/null || true"


def test_step_battery_state_positional_seed() -> None:
    """Test step_battery_state with positional seed argument."""
    state = generate_initial_battery_state(seed=123)
    stepped = step_battery_state(state, 456)
    assert stepped.level == state.level - 1


def test_decimal_to_dms_rounding_rollover() -> None:
    """Test degrees/minutes/seconds rollover on coordinate boundaries."""
    deg, minute, sec = _decimal_to_dms(51.9999999)
    assert int(deg) in (51, 52)
    assert 0 <= int(minute) < 60
    assert 0 <= float(sec) < 60.0


def test_photo_exif_gps_version_id_and_southern_hemisphere() -> None:
    """Test GPSVersionID and southern hemisphere coordinate refs."""
    spec = container_spec(_fingerprint("GooglePixel 7", "SDK 33"), "BR")
    rng = random.Random(789)  # noqa: S311
    now = datetime(2026, 10, 22, 14, 0, tzinfo=UTC)
    exif = photo_exif(spec, now, rng)

    gps = exif.get_ifd(IFD.GPSInfo)
    assert gps[GPS.GPSVersionID] == b"\x02\x03\x00\x00"
    assert gps[GPS.GPSLatitudeRef] == "S"
    assert gps[GPS.GPSLongitudeRef] == "W"


def test_local_apk_version_catalog(tmp_path: Path) -> None:
    """Test local APK version listing and traversal rejection."""
    tg_dir = tmp_path / "telegram"
    tg_dir.mkdir(parents=True)
    apk_11 = tg_dir / "11.4.2.apk"
    apk_11.write_bytes(b"fake-apk-binary-1")
    apk_10 = tg_dir / "10.9.0.apk"
    apk_10.write_bytes(b"fake-apk-binary-2")
    empty_apk = tg_dir / "0.0.0.apk"
    empty_apk.write_bytes(b"")

    other_dir = tmp_path / "whatsapp"
    other_dir.mkdir(parents=True)
    wa_apk = other_dir / "2.24.1.apk"
    wa_apk.write_bytes(b"fake-wa-binary")

    # List all
    all_versions = list_local_apk_versions(tmp_path)
    assert len(all_versions) == 3
    assert {v.app_slug for v in all_versions} == {"telegram", "whatsapp"}

    # Filter by app_slug
    tg_versions = list_local_apk_versions(tmp_path, "telegram")
    assert len(tg_versions) == 2
    assert [v.version for v in tg_versions] == ["10.9.0", "11.4.2"]

    # Resolve pinned
    resolved = resolve_pinned_apk_path(tmp_path, "telegram", "11.4.2")
    assert resolved == apk_11.resolve()

    # Missing version
    with pytest.raises(PinnedApkNotFoundError, match="Pinned APK version not found"):
        resolve_pinned_apk_path(tmp_path, "telegram", "99.0.0")

    # Empty 0-byte file
    with pytest.raises(PinnedApkNotFoundError, match="Pinned APK version not found"):
        resolve_pinned_apk_path(tmp_path, "telegram", "0.0.0")

    # Path traversal rejection in resolve_pinned_apk_path
    with pytest.raises(PinnedApkNotFoundError):
        resolve_pinned_apk_path(tmp_path, "../../etc", "passwd")

    # Cross-app traversal rejection in version argument
    with pytest.raises(PinnedApkNotFoundError):
        resolve_pinned_apk_path(tmp_path, "telegram", "../whatsapp/2.24.1")

    # Empty or dot app_slug rejection
    with pytest.raises(PinnedApkNotFoundError):
        resolve_pinned_apk_path(tmp_path, "", "11.4.2")
    with pytest.raises(PinnedApkNotFoundError):
        resolve_pinned_apk_path(tmp_path, ".", "11.4.2")

    # Path traversal and invalid app_slug rejection in list_local_apk_versions
    assert list_local_apk_versions(tmp_path, "../../etc") == []
    assert list_local_apk_versions(tmp_path, "") == []
    assert list_local_apk_versions(tmp_path, ".") == []
    assert list_local_apk_versions(tmp_path, "telegram/sub") == []
    assert list_local_apk_versions(tmp_path / "nonexistent") == []
    assert list_local_apk_versions(apk_11) == []

    # validate=True fails on fake non-zip content
    with pytest.raises(ValueError, match="no manifest"):
        resolve_pinned_apk_path(tmp_path, "telegram", "11.4.2", validate=True)


def test_verify_local_apk_with_valid_apk(tmp_path: Path) -> None:
    """Test verification of valid local APK identity and signing cert."""
    apk_file = tmp_path / "valid.apk"
    cert = b"test-der-cert"
    build_apk(
        apk_file,
        certificate=cert,
        package="org.telegram.messenger",
        version=("11.5.0", 55221),
    )
    identity, cert_sha256 = verify_local_apk(apk_file)
    assert identity.package == "org.telegram.messenger"
    assert identity.version == "11.5.0 (55221)"
    assert cert_sha256 == hashlib.sha256(cert).hexdigest()

    # resolve_pinned_apk_path with validate=True succeeds on valid apk
    app_store = tmp_path / "store"
    app_dir = app_store / "telegram"
    app_dir.mkdir(parents=True)
    pinned_file = app_dir / "11.5.0.apk"
    pinned_file.write_bytes(apk_file.read_bytes())
    resolved = resolve_pinned_apk_path(app_store, "telegram", "11.5.0", validate=True)
    assert resolved == pinned_file.resolve()


def test_local_apk_version_catalog_directory_escape(tmp_path: Path) -> None:
    """Test that list_local_apk_versions skips directories escaping the store."""
    store = tmp_path / "store"
    store.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_apk = outside / "1.0.0.apk"
    outside_apk.write_bytes(b"outside-apk-content")

    link_dir = store / "escaped_link"
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    versions = list_local_apk_versions(store)
    assert versions == []
