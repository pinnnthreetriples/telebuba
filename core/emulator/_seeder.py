"""A fresh container's own data: an address book and a camera roll, written once.

A new redroid container has no contacts and no photos, which no phone in use has.
On its first boot it gets both, from the container's identity so they agree with it:

* the proxy country's people (``_seed_data``), written through the Contacts Provider
  (``content insert``);
* the phone's photos (``_seed_data``) in ``/sdcard/DCIM/Camera``, each file's mtime set
  to its capture time, and MediaStore asked to index them.

A marker in ``/data/local/tmp`` is written once both are in: a container with it is
never seeded again, and a seeding cut short is redone at the next boot (contacts this
seeding wrote are deleted first, and so are the photos an earlier try listed, whatever
day they were dated).

Contacts take one ``content`` call each (an app_process start), so the scripts run
on the device as one ``sh`` file, not one ``adb shell`` per row. ``content`` exits 0
even when the provider refused a row, so every call goes through a wrapper that fails
on the error it prints, and a script stops at its first failure: the marker is never
written over a half-filled address book.
"""

from __future__ import annotations

import asyncio
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.config import settings
from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._seed_data import make_contacts, make_photos, rng_for
from core.emulator._text import shell_quote

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from core.emulator._seed_data import Contact, Photo

MARKER = "/data/local/tmp/.telebuba_seeded"
_SCRIPT = "/data/local/tmp/telebuba_seed.sh"
# The photos the latest try put in, so a redo removes them even when dated otherwise.
_PHOTO_LIST = "/data/local/tmp/.telebuba_photos"
CAMERA_DIR = "/sdcard/DCIM/Camera"

# ``content`` prints a provider's refusal (or its own usage error) and still exits 0:
# ``c`` is ``content`` failing on that. Every script starts with it.
_SCRIPT_HEAD = (
    "c() {",
    '  out=$(content "$@" 2>&1) || { printf \'%s\\n\' "$out" >&2; return 1; }',
    "  case $out in *rror*|*xception*) printf '%s\\n' \"$out\" >&2; return 1 ;; esac",
    '  [ -z "$out" ] || printf \'%s\\n\' "$out"',
    "}",
)

_CONTACTS = "content://com.android.contacts"
# Every raw contact this seeding writes carries it, so a redo can delete exactly those.
_SYNC_TAG = "telebuba"

# ContactsContract's TYPE_MOBILE / TYPE_HOME / TYPE_WORK.
_PHONE_MOBILE = 2
_EMAIL_HOME = 1
_ORG_WORK = 1


def _bind(column: str, kind: str, value: str) -> str:
    return "--bind " + shell_quote(f"{column}:{kind}:{value}")


def _data_row(mimetype: str, *binds: str) -> str:
    return " ".join(
        (
            f"c insert --uri {_CONTACTS}/data --bind raw_contact_id:i:$id",
            _bind("mimetype", "s", f"vnd.android.cursor.item/{mimetype}"),
            *binds,
            "|| exit 1",
        )
    )


def _fail(why: str) -> str:
    return "{ echo " + shell_quote(f"telebuba: {why}") + " >&2; exit 1; }"


def contacts_script(contacts: list[Contact]) -> list[str]:
    """The ``sh`` lines that replace this seeding's contacts with ``contacts``.

    Every line stops the script when it fails, and the last one counts the tagged
    rows: nothing ``content`` refused goes unnoticed.
    """
    tagged = shell_quote(f"sync1='{_SYNC_TAG}'")
    query = f"c query --uri {_CONTACTS}/raw_contacts --projection _id --where {tagged}"
    lines = [
        f"c delete --uri '{_CONTACTS}/raw_contacts?caller_is_syncadapter=true'"
        f" --where {tagged} || exit 1"
    ]
    for contact in contacts:
        lines += [
            f"c insert --uri {_CONTACTS}/raw_contacts --bind account_type:n:"
            f" --bind account_name:n: {_bind('sync1', 's', _SYNC_TAG)} || exit 1",
            # The raw contact just written: the newest one carrying the tag.
            f"id=$({query} --sort '_id DESC') || exit 1",
            """id=$(printf '%s\\n' "$id" | head -n 1 | sed 's/.*_id=//')""",
            f"case $id in ''|*[!0-9]*) {_fail('no raw contact id')} ;; esac",
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
    lines += [
        f"rows=$({query}) || exit 1",
        f"""[ "$(printf '%s\\n' "$rows" | grep -c '^Row:')" -eq {len(contacts)} ]"""
        f" || {_fail('contacts missing')}",
    ]
    return lines


def gallery_prep_script(photos: list[Photo]) -> list[str]:
    """The ``sh`` lines run before the push: an earlier try's photos out, these listed.

    The list is written before any file is pushed, so a try cut short anywhere leaves
    a list naming everything it may have put in.
    """
    names = " ".join(shell_quote(photo.name) for photo in photos)
    return [
        f"mkdir -p {CAMERA_DIR} || exit 1",
        f"if [ -f {_PHOTO_LIST} ]; then"
        f' while read -r f; do rm -f "{CAMERA_DIR}/$f"; done < {_PHOTO_LIST}; fi',
        f"printf '%s\\n' {names} > {_PHOTO_LIST} || exit 1",
    ]


def gallery_script(photos: list[Photo]) -> list[str]:
    """The ``sh`` lines that date the pushed photos and have MediaStore index them."""
    # Stamped in UTC under ``TZ=UTC0``, so the mtime is right whatever zone the device
    # is set to. ``-c`` creates nothing: a photo missing (or empty) fails the script.
    lines = []
    for photo in photos:
        path = f"{CAMERA_DIR}/{photo.name}"
        stamp = photo.taken.astimezone(UTC).strftime("%Y%m%d%H%M.%S")
        lines.append(f"TZ=UTC0 touch -c -t {stamp} {path} && [ -s {path} ] || exit 1")
    # ``scan_volume`` (MediaProvider's own call) indexes the volume; ``c`` fails when the
    # provider refuses it, and the per-file scan broadcast is the fallback.
    scan_files = " ".join(f"{CAMERA_DIR}/{photo.name}" for photo in photos)
    lines.append(
        "c call --uri content://media --method scan_volume --arg external_primary"
        f" >/dev/null || for f in {scan_files}; do"
        " am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://$f"
        " >/dev/null || exit 1; done"
    )
    return lines


async def _run_script(serial: str, lines: list[str]) -> None:
    """Push ``lines`` as one ``sh`` file, run it, and remove it whatever happened.

    The removal is best effort: on a device gone mid-script it fails too, and its error
    must not hide the script's own.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="telebuba-seed-") as scratch:
            local = Path(scratch) / "seed.sh"
            script = "\n".join((*_SCRIPT_HEAD, *lines)) + "\n"
            local.write_text(script, encoding="utf-8", newline="\n")
            await adb.push(serial, str(local), remote=_SCRIPT)
        await adb.shell(serial, "sh", _SCRIPT, seconds=settings.emulator.install_timeout_seconds)
    finally:
        with suppress(EmulatorError):
            await adb.shell(serial, "rm", "-f", _SCRIPT)


async def seed_contacts(serial: str, spec: ContainerSpec) -> None:
    """Write the contacts of ``spec``'s proxy country into the container's address book."""
    # Off the event loop: a sparse numbering plan keeps ``phonenumbers`` busy a while.
    contacts = await asyncio.to_thread(
        make_contacts, spec.region.country, rng_for(spec, "contacts")
    )
    await _run_script(serial, contacts_script(contacts))


def _write_photos(folder: Path, photos: list[Photo]) -> list[str]:
    paths = []
    for photo in photos:
        path = folder / photo.name
        path.write_bytes(photo.jpeg)
        paths.append(str(path))
    return paths


async def seed_gallery(serial: str, spec: ContainerSpec) -> None:
    """Put ``spec``'s phone's photos into ``DCIM/Camera``, dated, and have them indexed."""
    rng = rng_for(spec, "gallery")
    photos = await asyncio.to_thread(make_photos, spec, rng, datetime.now(UTC))
    await _run_script(serial, gallery_prep_script(photos))
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
