"""A fresh container's own data: an address book and a camera roll, written once.

A new redroid container has no contacts and no photos, which no phone in use has.
On its first boot it gets both, from the container's identity so they agree with it:

* 25-40 contacts with names of the proxy country's language (``configs/contact_names.json``)
  and that country's mobile numbers (``phonenumbers``), some with an e-mail and a
  company, written through the Contacts Provider (``content insert``);
* 12-20 JPEGs in ``/sdcard/DCIM/Camera`` named the way the phone's camera app names
  them, each with the EXIF that phone writes (maker, model, firmware, its main lens's
  aperture and focal length) and a capture time 5-45 days back in the container's
  timezone; the file's mtime is set to that time and MediaStore is asked to index them.

The data is drawn from the container's serial, so a container made again for the same
account gets the same people and photos. A marker in ``/data/local/tmp`` is written
once both are in: a container with it is never seeded again, and a seeding cut short
is redone at the next boot (contacts this seeding wrote are deleted first, photos
overwritten by name).

Contacts take one ``content`` call each (an app_process start), so the scripts run
on the device as one ``sh`` file, not one ``adb shell`` per row.
"""

from __future__ import annotations

import asyncio
import io
import json
import random
import string
import tempfile
import unicodedata
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
from zoneinfo import ZoneInfo

import phonenumbers
from PIL import Image, ImageDraw, ImageFilter
from PIL.ExifTags import IFD, Base
from PIL.TiffImagePlugin import IFDRational
from pydantic import BaseModel, ConfigDict, Field

from core.config import settings
from core.emulator import adb
from core.emulator._blueprints import CONFIGS_DIR
from core.emulator._text import shell_quote

if TYPE_CHECKING:
    from core.emulator._blueprints import Camera
    from core.emulator._profile import ContainerSpec

_NAMES_PATH = CONFIGS_DIR / "contact_names.json"
_DEFAULT_LOCALE = "en_US"
_DEFAULT_PHONE_REGION = "US"

MARKER = "/data/local/tmp/.telebuba_seeded"
_SCRIPT = "/data/local/tmp/telebuba_seed.sh"
CAMERA_DIR = "/sdcard/DCIM/Camera"

_CONTACTS = "content://com.android.contacts"
# Every raw contact this seeding writes carries it, so a redo can delete exactly those.
_SYNC_TAG = "telebuba"
_INTERNATIONAL_SHARE = 0.7
_EXTRA_SHARE = 0.22
_CONTACT_TRIES = 5
_PHONE_TRIES = 200
_PHONE_PREFIX_KEPT = (0, 2, 4)
_MOBILE_TYPES = {
    phonenumbers.PhoneNumberType.MOBILE,
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
}
# ContactsContract's TYPE_MOBILE / TYPE_HOME / TYPE_WORK.
_PHONE_MOBILE = 2
_EMAIL_HOME = 1
_ORG_WORK = 1

_CONTACT_COUNT = (25, 40)
_PHOTO_COUNT = (12, 20)
_PHOTO_DAYS_BACK = (5, 45)
# Local hours photos are taken in: nobody's camera roll is full of 3 a.m. shots.
_PHOTO_HOURS = (8, 21)
_PHOTO_SIZE = (1600, 1200)
_ELLIPSE_SHARE = 0.5
_JPEG_QUALITY = (88, 95)
_ISO = (50, 64, 80, 100, 125, 160, 200, 250, 320, 400, 640)
_EXPOSURE_DENOMINATORS = (30, 50, 60, 100, 120, 200, 250, 500, 1000)
_EXIF_TIME = "%Y:%m:%d %H:%M:%S"


class _NamePack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    countries: tuple[str, ...] = Field(min_length=1)
    first: tuple[str, ...] = Field(min_length=1)
    last: tuple[str, ...] = Field(min_length=1)
    companies: tuple[str, ...] = Field(min_length=1)
    domains: tuple[str, ...] = Field(min_length=1)


class Contact(NamedTuple):
    first: str
    last: str
    phone: str
    email: str | None
    company: str | None


class Photo(NamedTuple):
    name: str
    taken: datetime  # aware, in the container's timezone
    jpeg: bytes


@cache
def load_name_packs() -> dict[str, _NamePack]:
    """Locale -> its names; read once per process."""
    raw = json.loads(_NAMES_PATH.read_text(encoding="utf-8"))
    return {locale: _NamePack.model_validate(pack) for locale, pack in raw.items()}


def locale_for(country: str | None) -> str:
    """The name locale of ``country`` (ISO-3166 alpha-2), ``en_US`` when none fits."""
    code = (country or "").upper()
    packs = load_name_packs()
    return next((loc for loc, pack in packs.items() if code in pack.countries), _DEFAULT_LOCALE)


def _rng(spec: ContainerSpec, purpose: str) -> random.Random:
    return random.Random(f"{purpose}:{spec.serial}")  # noqa: S311  # nosec B311 - fixture data, not a secret


def mobile_number(country: str | None, rng: random.Random) -> phonenumbers.PhoneNumber:
    """A valid mobile number of ``country`` (the US when it has none on record)."""
    code = (country or "").upper()
    region = code if code in phonenumbers.SUPPORTED_REGIONS else _DEFAULT_PHONE_REGION
    example = phonenumbers.example_number_for_type(region, phonenumbers.PhoneNumberType.MOBILE)
    if example is None:  # a territory without mobile numbers on record
        return mobile_number(_DEFAULT_PHONE_REGION, rng)
    known = phonenumbers.national_significant_number(example)
    # Drawn whole first, so operator prefixes vary like a real address book's; a country
    # whose valid numbers are too sparse for that keeps more of the example's prefix. A
    # shared calling code (+1, +7) must still land in ``region``.
    for keep in _PHONE_PREFIX_KEPT:
        for _ in range(_PHONE_TRIES):
            tail = "".join(rng.choice(string.digits) for _ in range(len(known) - keep))
            number = phonenumbers.parse(f"+{example.country_code}{known[:keep]}{tail}")
            if (
                phonenumbers.is_valid_number(number)
                and phonenumbers.region_code_for_number(number) == region
                and phonenumbers.number_type(number) in _MOBILE_TYPES
            ):
                return number
    return example


def _ascii(text: str) -> str:
    """``text`` as plain lower-case letters and digits (empty for a non-Latin script)."""
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(char for char in folded.lower() if char.isalnum())


def make_contacts(
    country: str | None, rng: random.Random, count: int | None = None
) -> list[Contact]:
    """``count`` (25-40 when ``None``) people of ``country``, no two with one number.

    Fewer only where the country's numbers are too sparse to draw that many apart.
    """
    pack = load_name_packs()[locale_for(country)]
    total = rng.randint(*_CONTACT_COUNT) if count is None else count
    contacts: list[Contact] = []
    numbers: set[str] = set()
    for _ in range(total * _CONTACT_TRIES):
        if len(contacts) == total:
            break
        first, last = rng.choice(pack.first), rng.choice(pack.last)
        number = mobile_number(country, rng)
        e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        if e164 in numbers:
            continue
        numbers.add(e164)
        style = (
            phonenumbers.PhoneNumberFormat.INTERNATIONAL
            if rng.random() < _INTERNATIONAL_SHARE
            else phonenumbers.PhoneNumberFormat.NATIONAL
        )
        email = company = None
        if rng.random() < _EXTRA_SHARE:
            company = rng.choice(pack.companies)
            user = ".".join(part for part in (_ascii(first), _ascii(last)) if part)
            if user:
                email = f"{user}{rng.randint(1, 99)}@{rng.choice(pack.domains)}"
        contacts.append(
            Contact(first, last, phonenumbers.format_number(number, style), email, company)
        )
    return contacts


def _bind(column: str, kind: str, value: str) -> str:
    return "--bind " + shell_quote(f"{column}:{kind}:{value}")


def _data_row(mimetype: str, *binds: str) -> str:
    return " ".join(
        (
            f"content insert --uri {_CONTACTS}/data --bind raw_contact_id:i:$id",
            _bind("mimetype", "s", f"vnd.android.cursor.item/{mimetype}"),
            *binds,
        )
    )


def contacts_script(contacts: list[Contact]) -> list[str]:
    """The ``sh`` lines that replace this seeding's contacts with ``contacts``."""
    tagged = shell_quote(f"sync1='{_SYNC_TAG}'")
    lines = [
        f"content delete --uri '{_CONTACTS}/raw_contacts?caller_is_syncadapter=true'"
        f" --where {tagged}"
    ]
    for contact in contacts:
        lines += [
            f"content insert --uri {_CONTACTS}/raw_contacts --bind account_type:n:"
            f" --bind account_name:n: {_bind('sync1', 's', _SYNC_TAG)}",
            # The raw contact just written: the newest one carrying the tag.
            f"id=$(content query --uri {_CONTACTS}/raw_contacts --projection _id"
            f" --where {tagged} --sort '_id DESC' | head -n 1 | sed 's/.*_id=//')",
            _data_row(
                "name",
                _bind("data1", "s", f"{contact.first} {contact.last}"),
                _bind("data2", "s", contact.first),
                _bind("data3", "s", contact.last),
            ),
            _data_row(
                "phone_v2",
                _bind("data1", "s", contact.phone),
                _bind("data2", "i", str(_PHONE_MOBILE)),
            ),
        ]
        if contact.email:
            lines.append(
                _data_row(
                    "email_v2",
                    _bind("data1", "s", contact.email),
                    _bind("data2", "i", str(_EMAIL_HOME)),
                )
            )
        if contact.company:
            lines.append(
                _data_row(
                    "organization",
                    _bind("data1", "s", contact.company),
                    _bind("data2", "i", str(_ORG_WORK)),
                )
            )
    return lines


def photo_name(camera: Camera, taken: datetime, rng: random.Random) -> str:
    """The file name the phone's camera app gives a photo taken at ``taken``."""
    match camera.file_style:
        case "samsung":
            return taken.strftime("%Y%m%d_%H%M%S.jpg")
        case "pixel":  # Pixel names its photos in UTC, with milliseconds
            return (
                taken.astimezone(UTC).strftime("PXL_%Y%m%d_%H%M%S")
                + f"{rng.randrange(1000):03d}.jpg"
            )
        case "xiaomi":
            return taken.strftime("IMG_%Y%m%d_%H%M%S.jpg")
        case "oneplus":
            return taken.strftime("IMG%Y%m%d%H%M%S.jpg")


def _rational(value: float) -> IFDRational:
    return IFDRational(round(value * 100), 100)


def photo_exif(spec: ContainerSpec, taken: datetime, rng: random.Random) -> Image.Exif:
    """The EXIF ``spec``'s phone writes for a photo taken at ``taken`` (local, aware)."""
    stamp = taken.strftime(_EXIF_TIME)
    offset = taken.strftime("%z")
    exif = Image.Exif()
    exif[Base.Make] = spec.manufacturer
    exif[Base.Model] = spec.model
    exif[Base.Software] = spec.build.incremental
    exif[Base.DateTime] = stamp
    exif[Base.Orientation] = 1
    sub = exif.get_ifd(IFD.Exif)
    sub[Base.DateTimeOriginal] = stamp
    sub[Base.DateTimeDigitized] = stamp
    sub[Base.OffsetTimeOriginal] = f"{offset[:3]}:{offset[3:]}"
    sub[Base.FNumber] = _rational(spec.camera.f_number)
    sub[Base.FocalLength] = _rational(spec.camera.focal_length_mm)
    sub[Base.ISOSpeedRatings] = rng.choice(_ISO)
    sub[Base.ExposureTime] = IFDRational(1, rng.choice(_EXPOSURE_DENOMINATORS))
    sub[Base.Flash] = 0
    sub[Base.ExifImageWidth], sub[Base.ExifImageHeight] = _PHOTO_SIZE
    return exif


def _scene(rng: random.Random) -> Image.Image:
    """A blurred, grainy scene of its own: a few colour blobs blown up, shapes, sensor noise."""
    width, height = _PHOTO_SIZE

    def colour() -> tuple[int, int, int]:
        return (rng.randint(30, 230), rng.randint(30, 230), rng.randint(30, 230))

    base = Image.new("RGB", (4, 3))
    base.putdata([colour() for _ in range(12)])
    image = base.resize(_PHOTO_SIZE, Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(image)
    for _ in range(rng.randint(3, 8)):
        x, y = rng.randrange(width), rng.randrange(height)
        w, h = rng.randint(60, 400), rng.randint(60, 400)
        shape = draw.ellipse if rng.random() < _ELLIPSE_SHARE else draw.rectangle
        shape((x, y, x + w, y + h), fill=colour())
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(2.0, 6.0)))
    grain = Image.effect_noise(_PHOTO_SIZE, rng.uniform(20.0, 40.0)).convert("RGB")
    return Image.blend(image, grain, rng.uniform(0.04, 0.09))


def make_photos(
    spec: ContainerSpec, rng: random.Random, now: datetime, count: int | None = None
) -> list[Photo]:
    """``count`` (12-20 when ``None``) photos taken 5-45 days before ``now``, oldest first."""
    zone = ZoneInfo(spec.region.timezone)
    total = rng.randint(*_PHOTO_COUNT) if count is None else count
    today = now.astimezone(zone).replace(microsecond=0)
    taken_at = sorted(
        (today - timedelta(days=rng.randint(*_PHOTO_DAYS_BACK))).replace(
            hour=rng.randint(*_PHOTO_HOURS), minute=rng.randrange(60), second=rng.randrange(60)
        )
        for _ in range(total)
    )
    photos: list[Photo] = []
    for taken in taken_at:
        buffer = io.BytesIO()
        _scene(rng).save(
            buffer, "JPEG", quality=rng.randint(*_JPEG_QUALITY), exif=photo_exif(spec, taken, rng)
        )
        photos.append(Photo(photo_name(spec.camera, taken, rng), taken, buffer.getvalue()))
    return photos


def gallery_script(photos: list[Photo]) -> list[str]:
    """The ``sh`` lines that date the pushed photos and have MediaStore index them."""
    lines = [
        f"touch -t {photo.taken.strftime('%Y%m%d%H%M.%S')} {CAMERA_DIR}/{photo.name}"
        for photo in photos
    ]
    # ``scan_volume`` (Android 11+) indexes the whole volume; the broadcast is the fallback.
    scan_files = " ".join(f"{CAMERA_DIR}/{photo.name}" for photo in photos)
    lines.append(
        "content call --uri content://media --method scan_volume --arg external_primary"
        f" || for f in {scan_files}; do"
        " am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://$f; done"
    )
    return lines


async def _run_script(serial: str, lines: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="telebuba-seed-") as scratch:
        local = Path(scratch) / "seed.sh"
        local.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        await adb.push(serial, str(local), remote=_SCRIPT)
    try:
        await adb.shell(serial, "sh", _SCRIPT, seconds=settings.emulator.install_timeout_seconds)
    finally:
        await adb.shell(serial, "rm", "-f", _SCRIPT)


async def seed_contacts(serial: str, spec: ContainerSpec, count: int | None = None) -> None:
    """Write the contacts of ``spec``'s proxy country into the container's address book."""
    contacts = make_contacts(spec.region.country, _rng(spec, "contacts"), count)
    await _run_script(serial, contacts_script(contacts))


def _write_photos(folder: Path, photos: list[Photo]) -> list[str]:
    paths = []
    for photo in photos:
        path = folder / photo.name
        path.write_bytes(photo.jpeg)
        paths.append(str(path))
    return paths


async def seed_gallery(
    serial: str, spec: ContainerSpec, count: int | None = None, *, now: datetime | None = None
) -> None:
    """Put ``spec``'s phone's photos into ``DCIM/Camera``, dated, and have them indexed."""
    rng = _rng(spec, "gallery")
    photos = await asyncio.to_thread(make_photos, spec, rng, now or datetime.now(UTC), count)
    await adb.shell(serial, "mkdir", "-p", CAMERA_DIR)
    with tempfile.TemporaryDirectory(prefix="telebuba-gallery-") as scratch:
        paths = await asyncio.to_thread(_write_photos, Path(scratch), photos)
        await adb.push(serial, *paths, remote=f"{CAMERA_DIR}/")
    await _run_script(serial, gallery_script(photos))


async def is_seeded(serial: str) -> bool:
    folder, _, name = MARKER.rpartition("/")
    listed = await adb.shell(serial, "ls", "-a", folder)
    return name in {line.strip() for line in listed.splitlines()}


async def seed_device_if_needed(serial: str, spec: ContainerSpec) -> bool:
    """Seed a booted container that has never been seeded; ``True`` when it was seeded now."""
    if await is_seeded(serial):
        return False
    await seed_contacts(serial, spec)
    await seed_gallery(serial, spec)
    await adb.shell(serial, "touch", MARKER)
    return True
