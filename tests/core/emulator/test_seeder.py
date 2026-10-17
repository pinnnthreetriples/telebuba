"""A fresh container's contacts and camera roll: the country's people, the phone's photos, once."""

from __future__ import annotations

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
from core.emulator import _seeder
from core.emulator._blueprints import load_blueprints
from core.emulator._errors import EmulatorError
from core.emulator._profile import container_spec
from core.emulator._seeder import (
    CAMERA_DIR,
    MARKER,
    contacts_script,
    gallery_script,
    locale_for,
    make_contacts,
    make_photos,
    photo_name,
    seed_device_if_needed,
)

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console

_SERIAL = "127.0.0.1:5600"
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
    pack = _seeder.load_name_packs()[locale]

    contacts = make_contacts(country, _random(7), 40)

    assert len(contacts) == 40
    numbers = set()
    for contact in contacts:
        assert contact.first in pack.first
        assert contact.last in pack.last
        number = phonenumbers.parse(contact.phone, region)
        assert phonenumbers.is_valid_number(number), contact.phone
        assert phonenumbers.region_code_for_number(number) == region
        assert phonenumbers.number_type(number) in _seeder._MOBILE_TYPES
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
        _seeder.Contact("Jürgen", "O'Neil", "+49 1511 2345678", "j.oneil@gmx.de", "Bäckerei"),
        _seeder.Contact("Anna", "Koch", "01512 3456789", None, None),
    ]

    lines = contacts_script(contacts)

    assert lines[0].startswith("content delete --uri ")
    assert "caller_is_syncadapter=true" in lines[0]
    raw = [line for line in lines if "/raw_contacts --bind" in line]
    assert len(raw) == 2
    assert all("'sync1:s:telebuba'" in line for line in raw)
    data = [line for line in lines if "/data --bind raw_contact_id:i:$id" in line]
    assert len(data) == 2 + 2 + 1 + 1  # name and phone each, then Jürgen's e-mail and company
    # A quote in a name closes, escapes and reopens; nothing else is interpreted by sh.
    assert "--bind 'data1:s:Jürgen O'\\''Neil'" in lines[3]
    assert "'mimetype:s:vnd.android.cursor.item/phone_v2'" in lines[4]
    assert "'data2:i:2'" in lines[4]
    assert any("email_v2" in line and "j.oneil@gmx.de" in line for line in lines)
    assert any("organization" in line and "Bäckerei" in line for line in lines)


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
        assert image.size == _seeder._PHOTO_SIZE
        stamp = photo.taken.strftime("%Y:%m:%d %H:%M:%S")
        assert exif[Base.Make] == spec.manufacturer
        assert exif[Base.Model] == spec.model
        assert exif[Base.Software] == spec.build.incremental
        assert exif[Base.DateTime] == stamp
        sub = exif.get_ifd(IFD.Exif)
        assert sub[Base.DateTimeOriginal] == stamp
        assert sub[Base.DateTimeDigitized] == stamp
        assert float(sub[Base.FNumber]) == pytest.approx(spec.camera.f_number)
        assert float(sub[Base.FocalLength]) == pytest.approx(spec.camera.focal_length_mm)
        assert sub[Base.ISOSpeedRatings] in _seeder._ISO


def test_the_same_account_gets_the_same_photos_and_people() -> None:
    spec = _spec()

    first = _seeder._rng(spec, "gallery").random(), _seeder._rng(spec, "contacts").random()
    again = _seeder._rng(spec, "gallery").random(), _seeder._rng(spec, "contacts").random()

    assert first == again
    assert first[0] != first[1]


def test_the_gallery_script_dates_every_file_then_has_mediastore_index_them() -> None:
    photos = make_photos(_spec(), _random(2), _NOW, 2)

    lines = gallery_script(photos)

    for photo, line in zip(photos, lines, strict=False):
        stamp = photo.taken.strftime("%Y%m%d%H%M.%S")
        assert line == f"touch -t {stamp} {CAMERA_DIR}/{photo.name}"
    assert "--method scan_volume --arg external_primary" in lines[-1]
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
    assert ["mkdir", "-p", CAMERA_DIR] in shells
    assert shells.count(["sh", _seeder._SCRIPT]) == 2  # contacts, then the gallery's dates
    pushes = [call for call in console.calls if "push" in call]
    assert pushes[1][-1] == f"{CAMERA_DIR}/"
    assert 12 <= len(pushes[1]) - 5 <= 20  # adb -s <serial> push <files...> <dir>
    jpeg_start = bytes.fromhex("ffd8")
    assert heads.count(jpeg_start) == len(pushes[1]) - 5, "the photos pushed are JPEGs"


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
    assert shells[-1] == ["rm", "-f", _seeder._SCRIPT]


def test_a_country_with_too_few_numbers_gets_fewer_contacts_not_a_hang(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only = phonenumbers.parse("+4915112345678")
    monkeypatch.setattr(_seeder, "mobile_number", lambda _country, _rng: only)

    contacts = make_contacts("DE", _random(1), 30)

    assert len(contacts) == 1
