"""A fresh container's contacts and camera roll: the country's people, the phone's photos, once."""

from __future__ import annotations

import asyncio
import io
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import phonenumbers
import pytest
from PIL import Image
from PIL.ExifTags import IFD, Base

from core.device_fingerprint_android import generate_android_fingerprint
from core.emulator import _seed_data, _seeder
from core.emulator._blueprints import load_blueprints
from core.emulator._errors import EmulatorError
from core.emulator._profile import container_spec
from core.emulator._seed_data import locale_for, make_contacts, make_photos, photo_name
from core.emulator._seeder import (
    CAMERA_DIR,
    MARKER,
    contacts_script,
    gallery_script,
    seed_device_if_needed,
)

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_SERIAL = "127.0.0.1:5600"
_SCRIPT = _seeder._SCRIPT
_GALLERY = _seeder._GALLERY_SCRIPT
_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _random(seed: int) -> random.Random:
    return random.Random(seed)  # noqa: S311 - reproducible fixture data


def _spec(model: str = "samsungSM-S911B", sdk: str = "SDK 34", country: str = "DE"):
    fingerprint = generate_android_fingerprint("acc", None).model_copy(
        update={"device_model": model, "system_version": sdk, "system_lang_code": "de-DE"},
    )
    return container_spec(fingerprint, country)


@pytest.mark.parametrize(
    ("country", "locale", "region"),
    [
        ("DE", "de_DE", "DE"),
        ("at", "de_DE", "AT"),
        ("GB", "en_GB", "GB"),
        ("BR", "pt_BR", "BR"),
        ("RU", "ru_RU", "RU"),
        ("UA", "uk_UA", "UA"),
        ("MX", "es_ES", "MX"),
        ("JP", "en_US", "JP"),  # no names of its own, numbers still its own
        (None, "en_US", "US"),
    ],
)
def test_contacts_are_the_countrys_people_with_its_mobile_numbers(
    country: str | None, locale: str, region: str
) -> None:
    assert locale_for(country) == locale
    pack = _seed_data.load_name_packs()[locale]

    contacts = make_contacts(country, _random(7), 40)

    assert len(contacts) == 40
    numbers = set()
    for contact in contacts:
        assert contact.first in pack.first
        assert contact.last in pack.last
        number = phonenumbers.parse(contact.phone, region)
        assert phonenumbers.is_valid_number(number), contact.phone
        assert phonenumbers.region_code_for_number(number) == region
        assert phonenumbers.number_type(number) in _seed_data._MOBILE_TYPES
        numbers.add(phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164))
        if contact.email is not None:
            assert contact.email.isascii()
            assert contact.email.rsplit("@", 1)[1] in pack.domains
        assert contact.company is None or contact.company in pack.companies
    assert len(numbers) == 40, "no two contacts share a number"
    assert {c.phone.startswith("+") for c in contacts} == {True, False}, "both formats appear"
    assert len({c.phone.split()[1] for c in contacts if c.phone.startswith("+")}) > 1


def test_a_default_address_book_has_25_to_40_people_and_some_extras() -> None:
    contacts = make_contacts("DE", _random(3))

    assert 25 <= len(contacts) <= 40
    assert any(c.email for c in contacts)
    assert any(c.company for c in contacts)


def test_the_contacts_script_replaces_only_this_seedings_rows() -> None:
    contacts = [
        _seed_data.Contact("Jürgen", "O'Neil", "+49 1511 2345678", "j.oneil@gmx.de", "Bäckerei"),
        _seed_data.Contact("Anna", "Koch", "01512 3456789", None, None),
    ]

    lines = contacts_script(contacts)

    assert lines[0].startswith("c delete --uri ")
    assert "caller_is_syncadapter=true" in lines[0]
    raw = [line for line in lines if "/raw_contacts --bind" in line]
    assert len(raw) == 2
    assert all("'sync1:s:telebuba'" in line for line in raw)
    # Each raw contact carries its own place and is found again by it, not as the newest.
    assert ["'sync2:s:1'" in raw[0], "'sync2:s:2'" in raw[1]] == [True, True]
    ids = [line for line in lines if line.startswith("id=$(c query")]
    assert ["sync2='\\''1'\\''" in ids[0], "sync2='\\''2'\\''" in ids[1]] == [True, True]
    data = [line for line in lines if "/data --bind raw_contact_id:i:$id" in line]
    assert len(data) == 2 + 2 + 1 + 1  # name and phone each, then Jürgen's e-mail and company
    # A quote in a name closes, escapes and reopens; nothing else is interpreted by sh.
    assert "--bind 'data1:s:Jürgen O'\\''Neil'" in data[0]
    assert "'mimetype:s:vnd.android.cursor.item/phone_v2'" in data[1]
    assert "'data2:i:2'" in data[1]
    # ``content`` exits 0 on a refusal: every call is the failing wrapper, and stops.
    calls = [line for line in lines if line.startswith(("c ", "id=$(c ", "rows=$(c "))]
    assert len(calls) == 1 + 2 * 2 + len(data) + 1
    assert all(line.endswith("|| exit 1") for line in calls)
    assert "-eq 2 ]" in lines[-1], "the script ends by counting every contact in"
    assert any("email_v2" in line and "j.oneil@gmx.de" in line for line in lines)
    assert any("organization" in line and "Bäckerei" in line for line in lines)


def test_the_contacts_script_writes_eight_lanes_at_once_then_waits_for_each() -> None:
    contacts = make_contacts("DE", _random(3), 30)

    lines = contacts_script(contacts)

    assert lines.count("(") == 8
    assert lines.count(') & pids="$pids $!"') == 8
    # A bare ``wait`` returns 0 whatever a lane did: each lane's own status is waited on.
    assert 'for p in $pids; do wait "$p" || failed=1; done' in lines
    wait = lines.index('for p in $pids; do wait "$p" || failed=1; done')
    assert lines[wait + 1].startswith('[ "$failed" -eq 0 ] ||')
    assert "-eq 30 ]" in lines[-1], "counted only once every lane is done"
    raw = [line for line in lines if "/raw_contacts --bind" in line]
    assert len({line.rsplit("'sync2:s:", 1)[1] for line in raw}) == 30


def test_a_short_address_book_writes_one_lane_per_contact() -> None:
    contacts = make_contacts("DE", _random(3), 3)

    assert contacts_script(contacts).count("(") == 3


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        ("samsung", "20261003_143022.jpg"),
        ("xiaomi", "IMG_20261003_143022.jpg"),
        ("oneplus", "IMG20261003143022.jpg"),
        ("pixel", "PXL_20261003_123022"),  # UTC, then milliseconds
    ],
)
def test_photos_are_named_the_way_the_phones_camera_names_them(style: str, expected: str) -> None:
    blueprint = next(b for b in load_blueprints() if b.camera.file_style == style)
    taken = datetime(2026, 10, 3, 14, 30, 22, tzinfo=ZoneInfo("Europe/Berlin"))

    name = photo_name(blueprint.camera, taken, _random(1))

    assert name.startswith(expected)
    assert name.endswith(".jpg")


def _exif(jpeg: bytes) -> tuple[Image.Image, Image.Exif]:
    image = Image.open(io.BytesIO(jpeg))
    return image, image.getexif()


@pytest.mark.parametrize(
    ("model", "sdk", "country"),
    [("samsungSM-S911B", "SDK 34", "DE"), ("GooglePixel 7", "SDK 33", "US")],
)
def test_photos_carry_the_phones_exif_and_a_local_capture_time(
    model: str, sdk: str, country: str
) -> None:
    spec = _spec(model, sdk, country)

    photos = make_photos(spec, _random(5), _NOW, 4)

    assert len(photos) == 4
    assert [p.taken for p in photos] == sorted(p.taken for p in photos)
    assert len({p.jpeg for p in photos}) == 4, "every photo is its own"
    for photo in photos:
        assert timedelta(days=5) <= _NOW - photo.taken <= timedelta(days=46)
        assert str(photo.taken.tzinfo) == spec.region.timezone
        image, exif = _exif(photo.jpeg)
        assert image.format == "JPEG"
        assert image.size == _seed_data._PHOTO_SIZE
        stamp = photo.taken.strftime("%Y:%m:%d %H:%M:%S")
        assert exif[Base.Make] == spec.manufacturer
        assert exif[Base.Model] == spec.model
        if spec.manufacturer == "samsung":
            assert exif[Base.Software] == spec.build.incremental
        else:
            assert Base.Software not in exif, "no made-up camera app version"
        assert exif[Base.DateTime] == stamp
        sub = exif.get_ifd(IFD.Exif)
        assert sub[Base.DateTimeOriginal] == stamp
        assert sub[Base.DateTimeDigitized] == stamp
        assert sub[Base.ExifVersion] == b"0232"
        offset = photo.taken.strftime("%z")
        for tag in (Base.OffsetTime, Base.OffsetTimeOriginal, Base.OffsetTimeDigitized):
            assert sub[tag] == f"{offset[:3]}:{offset[3:]}"
        assert float(sub[Base.FNumber]) == pytest.approx(spec.camera.f_number)
        assert float(sub[Base.FocalLength]) == pytest.approx(spec.camera.focal_length_mm)
        assert sub[Base.ISOSpeedRatings] in _seed_data._ISO


def test_the_same_account_gets_the_same_photos_and_people() -> None:
    spec = _spec()

    first = (
        _seed_data.rng_for(spec, "gallery").random(),
        _seed_data.rng_for(spec, "contacts").random(),
    )
    again = (
        _seed_data.rng_for(spec, "gallery").random(),
        _seed_data.rng_for(spec, "contacts").random(),
    )

    assert first == again
    assert first[0] != first[1]


def test_the_gallery_script_dates_every_file_then_has_mediastore_index_them() -> None:
    photos = make_photos(_spec(), _random(2), _NOW, 2)

    lines = gallery_script(photos)

    for photo, line in zip(photos, lines, strict=False):
        # Stamped in UTC under TZ=UTC0: right whatever zone the device is set to.
        stamp = photo.taken.astimezone(UTC).strftime("%Y%m%d%H%M.%S")
        path = f"{CAMERA_DIR}/{photo.name}"
        assert line == f"TZ=UTC0 touch -c -t {stamp} {path} && [ -s {path} ] || exit 1"
    assert lines[-1].startswith("c call --uri content://media --method scan_volume")
    assert "MEDIA_SCANNER_SCAN_FILE" in lines[-1], "the broadcast when scan_volume fails"


def _device(*, seeded: bool):
    def answer(argv: list[str]) -> str:
        if argv[-2:] == ["-a", "/data/local/tmp"]:
            return ".\n..\n.telebuba_seeded\n" if seeded else ".\n..\n"
        return ""

    return answer


@pytest.mark.asyncio
async def test_a_fresh_container_is_seeded_then_marked(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    console.answer = _device(seeded=False)
    heads: list[bytes] = []
    real_push = _seeder.adb.push

    async def push(serial: str, *local: str, remote: str) -> None:
        heads.extend(Path(path).read_bytes()[:2] for path in local)
        await real_push(serial, *local, remote=remote)

    monkeypatch.setattr(_seeder.adb, "push", push)

    assert await seed_device_if_needed(_SERIAL, _spec()) is True

    shells = console.shell_calls()
    assert shells[0] == ["ls", "-a", "/data/local/tmp"]
    assert shells[-1] == ["touch", MARKER], "marked only once both are in"
    assert shells.count(["sh", _SCRIPT]) == 1, "the contacts"
    # The gallery's prep (an earlier try's photos out), then its dates, in its own file.
    assert shells.count(["sh", _GALLERY]) == 2
    assert ["rm", "-f", _SCRIPT] in shells
    assert ["rm", "-f", _GALLERY] in shells
    pushes = [call for call in console.calls if "push" in call]
    gallery = [push[-1] for push in pushes if push[-1] != _SCRIPT]
    assert gallery == [_GALLERY, f"{CAMERA_DIR}/", _GALLERY]
    photos = next(push for push in pushes if push[-1] == f"{CAMERA_DIR}/")
    assert 12 <= len(photos) - 5 <= 20  # adb -s <serial> push <files...> <dir>
    jpeg_start = bytes.fromhex("ffd8")
    assert heads.count(jpeg_start) == len(photos) - 5, "the photos pushed are JPEGs"


@pytest.mark.asyncio
async def test_the_photos_are_made_and_pushed_while_the_contacts_are_written(
    console: Console, monkeypatch: pytest.MonkeyPatch
) -> None:
    console.answer = _device(seeded=False)
    pushed = asyncio.Event()
    real_push, real_shell = _seeder.adb.push, _seeder.adb.shell

    async def push(serial: str, *local: str, remote: str) -> None:
        await real_push(serial, *local, remote=remote)
        if remote == f"{CAMERA_DIR}/":
            pushed.set()

    async def shell(serial: str, *args: str, seconds: float | None = None) -> str:
        if list(args) == ["sh", _SCRIPT]:
            # One after the other, the photos would wait for this script: it never ends.
            await asyncio.wait_for(pushed.wait(), timeout=10)
        return await real_shell(serial, *args, seconds=seconds)

    monkeypatch.setattr(_seeder.adb, "push", push)
    monkeypatch.setattr(_seeder.adb, "shell", shell)

    assert await seed_device_if_needed(_SERIAL, _spec()) is True

    assert console.shell_calls()[-1] == ["touch", MARKER]


@pytest.mark.asyncio
async def test_a_failed_gallery_still_lets_the_contacts_finish_and_leaves_no_marker(
    console: Console,
) -> None:
    def answer(argv: list[str]) -> str:
        if argv[-1] == f"{CAMERA_DIR}/":
            code = "emulator_command_failed"
            raise EmulatorError(code)
        return _device(seeded=False)(argv)

    console.answer = answer

    with pytest.raises(EmulatorError) as raised:
        await seed_device_if_needed(_SERIAL, _spec())

    assert raised.value.code == "emulator_command_failed"
    shells = console.shell_calls()
    assert ["sh", _SCRIPT] in shells, "the contacts ran to their end"
    assert ["rm", "-f", _SCRIPT] in shells
    assert ["rm", "-f", _GALLERY] in shells
    assert ["touch", MARKER] not in shells


@pytest.mark.asyncio
async def test_a_marked_container_is_never_seeded_again(console: Console) -> None:
    console.answer = _device(seeded=True)

    assert await seed_device_if_needed(_SERIAL, _spec()) is False

    assert console.shell_calls() == [["ls", "-a", "/data/local/tmp"]]
    assert not [call for call in console.calls if "push" in call]


@pytest.mark.asyncio
async def test_a_failed_seeding_leaves_no_marker_and_no_script(console: Console) -> None:
    def answer(argv: list[str]) -> str:
        if argv[-2:] == ["sh", _seeder._SCRIPT]:
            code = "emulator_command_timeout"
            raise EmulatorError(code)
        return _device(seeded=False)(argv)

    console.answer = answer

    with pytest.raises(EmulatorError):
        await seed_device_if_needed(_SERIAL, _spec())

    shells = console.shell_calls()
    assert ["touch", MARKER] not in shells
    assert shells[-1][:2] == ["rm", "-f"]
    assert ["rm", "-f", _SCRIPT] in shells


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["push", "sh"])
async def test_the_script_is_removed_and_its_own_error_kept_whatever_failed(
    console: Console, failing: str
) -> None:
    def answer(argv: list[str]) -> str:
        if failing in argv:
            code = "emulator_command_timeout"
            raise EmulatorError(code)
        if argv[-3:-1] == ["rm", "-f"]:
            code = "emulator_command_failed"  # the device is gone by now
            raise EmulatorError(code)
        return _device(seeded=False)(argv)

    console.answer = answer

    with pytest.raises(EmulatorError) as raised:
        await seed_device_if_needed(_SERIAL, _spec())

    assert raised.value.code == "emulator_command_timeout"
    shells = console.shell_calls()
    assert ["rm", "-f", _SCRIPT] in shells
    assert ["rm", "-f", _GALLERY] in shells


@pytest.mark.parametrize("country", ["AX", "VA", "IM", "CC"])
def test_a_territory_sharing_its_calling_code_gets_a_full_address_book(country: str) -> None:
    contacts = make_contacts(country, _random(11), 30)

    assert len(contacts) == 30
    for contact in contacts:
        number = phonenumbers.parse(contact.phone, country)
        assert phonenumbers.is_valid_number_for_region(number, country), contact.phone


def test_a_country_with_too_few_numbers_gets_fewer_contacts_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only = phonenumbers.parse("+4915112345678")
    monkeypatch.setattr(_seed_data, "mobile_number", lambda _country, _rng: only)

    contacts = make_contacts("DE", _random(1), 30)

    assert len(contacts) == 1
