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

Every ``content`` call starts an app_process (about a second, mostly waiting), so the
scripts run on the device as ``sh`` files, not one ``adb shell`` per row, and the
contacts go in over ``_LANES`` background lanes at once. The photos are made, pushed and
indexed while the contacts are written. ``content`` exits 0 even when the provider
refused a row, so every call goes through a wrapper that fails on the error it prints;
a lane stops at its first failure and fails the script once every lane is done: the
marker is never written over a half-filled address book.
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
from core.emulator._seed_data import (
    build_call_and_sms_seed_commands,
    generate_call_and_sms_history,
    make_contacts,
    make_photos,
    rng_for,
)
from core.emulator._text import shell_quote

if TYPE_CHECKING:
    from core.emulator._profile import ContainerSpec
    from core.emulator._seed_data import Contact, Photo

MARKER = "/data/local/tmp/.telebuba_seeded"
# One file each: the contacts' script runs while the gallery's are pushed and run.
_SCRIPT = "/data/local/tmp/telebuba_seed.sh"
_GALLERY_SCRIPT = "/data/local/tmp/telebuba_gallery.sh"
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
# Every raw contact this seeding writes carries it, so a redo can delete exactly those;
# ``sync2`` holds the contact's place in the list, so each finds its own raw contact.
_SYNC_TAG = "telebuba"
# Contacts written at once: each ``content`` call mostly waits, and 8 lanes wrote 40
# contacts in 25 s on a redroid 13 pod, against ~160 s one after another.
_LANES = 8

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


def _query(where: str) -> str:
    return f"c query --uri {_CONTACTS}/raw_contacts --projection _id --where {where}"


def _contact_lines(index: int, contact: Contact) -> list[str]:
    """One contact's lines: its raw contact, found again by its own tag, then its rows."""
    own = shell_quote(f"sync1='{_SYNC_TAG}' AND sync2='{index}'")
    lines = [
        f"c insert --uri {_CONTACTS}/raw_contacts --bind account_type:n:"
        f" --bind account_name:n: {_bind('sync1', 's', _SYNC_TAG)}"
        f" {_bind('sync2', 's', str(index))} || exit 1",
        f"id=$({_query(own)}) || exit 1",
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
    return lines


def contacts_script(contacts: list[Contact]) -> list[str]:
    """The ``sh`` lines that replace this seeding's contacts with ``contacts``.

    The contacts are dealt over ``_LANES`` subshells run in the background, each writing
    its own one after another and stopping at its first failure. The script waits for
    every lane, fails when any did, and ends by counting the tagged rows: nothing
    ``content`` refused goes unnoticed.
    """
    tagged = shell_quote(f"sync1='{_SYNC_TAG}'")
    lines = [
        f"c delete --uri '{_CONTACTS}/raw_contacts?caller_is_syncadapter=true'"
        f" --where {tagged} || exit 1",
        "pids=",
    ]
    numbered = list(enumerate(contacts, start=1))
    for lane in range(min(_LANES, len(contacts))):
        lines.append("(")
        for index, contact in numbered[lane::_LANES]:
            lines += _contact_lines(index, contact)
        lines.append(') & pids="$pids $!"')
    lines += [
        # ``wait`` on each lane's pid gives its status; a bare ``wait`` would not.
        "failed=0",
        'for p in $pids; do wait "$p" || failed=1; done',
        f'[ "$failed" -eq 0 ] || {_fail("a contact was not written")}',
        f"rows=$({_query(tagged)}) || exit 1",
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


async def _run_script(serial: str, lines: list[str], remote: str = _SCRIPT) -> None:
    """Push ``lines`` as the ``sh`` file ``remote``, run it, and remove it whatever happened.

    The removal is best effort: on a device gone mid-script it fails too, and its error
    must not hide the script's own.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="telebuba-seed-") as scratch:
            local = Path(scratch) / "seed.sh"
            script = "\n".join((*_SCRIPT_HEAD, *lines)) + "\n"
            local.write_text(script, encoding="utf-8", newline="\n")
            await adb.push(serial, str(local), remote=remote)
        await adb.shell(serial, "sh", remote, seconds=settings.emulator.install_timeout_seconds)
    finally:
        with suppress(EmulatorError):
            await adb.shell(serial, "rm", "-f", remote)


async def seed_contacts(serial: str, spec: ContainerSpec) -> None:
    """Write the contacts and telephony history of ``spec``'s proxy country."""
    # Off the event loop: a sparse numbering plan keeps ``phonenumbers`` busy a while.
    contacts = await asyncio.to_thread(
        make_contacts, spec.region.country, rng_for(spec, "contacts")
    )
    calls, sms_list = await asyncio.to_thread(
        generate_call_and_sms_history,
        contacts,
        seed=f"telephony:{spec.serial}",
        locale=spec.region.locale,
    )
    script_lines = contacts_script(contacts) + build_call_and_sms_seed_commands(calls, sms_list)
    await _run_script(serial, script_lines)


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
    await _run_script(serial, gallery_prep_script(photos), _GALLERY_SCRIPT)
    with tempfile.TemporaryDirectory(prefix="telebuba-gallery-") as scratch:
        paths = await asyncio.to_thread(_write_photos, Path(scratch), photos)
        await adb.push(serial, *paths, remote=f"{CAMERA_DIR}/")
    await _run_script(serial, gallery_script(photos), _GALLERY_SCRIPT)


async def is_seeded(serial: str) -> bool:
    folder, _, name = MARKER.rpartition("/")
    listed = await adb.shell(serial, "ls", "-a", folder)
    return name in {line.strip() for line in listed.splitlines()}


async def seed_device_if_needed(serial: str, spec: ContainerSpec) -> bool:
    """Seed a booted container that has never been seeded; ``True`` when it was seeded now."""
    if await is_seeded(serial):
        return False
    # The photos are made, pushed and indexed while the contacts are written. Both run
    # to their end before either's error is raised: a script still writing on the
    # device must not outlive the seeding that started it.
    results = await asyncio.gather(
        seed_contacts(serial, spec), seed_gallery(serial, spec), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    await adb.shell(serial, "touch", MARKER)
    return True
