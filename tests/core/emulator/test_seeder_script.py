"""The seeding's contacts script under a real ``sh``, ``content`` faked as the device's.

Android's ``content`` exits 0 even when the provider refused a row and only prints
the error, so the script must stop on that print and the marker must never follow.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

import pytest

from core.emulator import _seeder
from core.emulator._seeder import _SCRIPT_HEAD, Contact, Photo, contacts_script, gallery_prep_script

if TYPE_CHECKING:
    from pathlib import Path

_SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(_SH is None, reason="needs a POSIX sh")

# A shell function wins over the binary: raw contacts count up in $STATE, a query
# lists them newest first, and an insert whose argv holds $FAIL_ON prints the
# provider's error and exits 0, as the real tool does.
_FAKE_CONTENT = """
content() {
  echo "$*" >> "$WORK/calls.log"
  case "$1" in
    insert)
      case "$*" in
        *"$FAIL_ON"*) echo "Error while accessing provider:com.android.contacts" >&2 ;;
        *raw_contacts*) echo $(( $(cat "$STATE") + $GROW )) > "$STATE" ;;
      esac ;;
    query)
      i=$(cat "$STATE")
      while [ "$i" -gt 0 ]; do echo "Row: 0 _id=$i"; i=$((i - 1)); done ;;
  esac
  return 0
}
"""

_PEOPLE = [
    Contact("Jürgen", "O'Neil", "+49 1511 2345678", "j.oneil@gmx.de", "Bäckerei"),
    Contact("Anna", "Koch", "01512 3456789", None, None),
]


class _Run(NamedTuple):
    status: int
    calls: list[str]
    stderr: str


def _run(tmp_path: Path, lines: list[str], *, fail_on: str = "never-matches", grow: int = 1):
    state = tmp_path / "state"
    state.write_text("0\n", encoding="utf-8")
    script = tmp_path / "seed.sh"
    script.write_text(
        "\n".join((_FAKE_CONTENT, *_SCRIPT_HEAD, *lines)) + "\n", encoding="utf-8", newline="\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "WORK": tmp_path.as_posix(),
        "STATE": state.as_posix(),
        "FAIL_ON": fail_on,
        "GROW": str(grow),
    }
    assert _SH is not None
    done = subprocess.run(  # noqa: S603 - test-owned argv
        [_SH, script.as_posix()], env=env, check=False, timeout=30, capture_output=True
    )
    log = tmp_path / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return _Run(done.returncode, calls, done.stderr.decode("utf-8", errors="replace"))


def test_every_contact_goes_in_under_its_own_raw_contact(tmp_path: Path) -> None:
    run = _run(tmp_path, contacts_script(_PEOPLE))

    assert run.status == 0, run.stderr
    data = [
        call
        for call in run.calls
        if call.startswith("insert --uri content://com.android.contacts/data")
    ]
    assert len(data) == 2 + 2 + 1 + 1
    assert all("raw_contact_id:i:1 " in call for call in data[:4])
    assert all("raw_contact_id:i:2 " in call for call in data[4:])
    # Quoted for sh, the name reaches ``content`` whole, quote and umlaut included.
    assert "data1:s:Jürgen O'Neil" in data[0]


def test_a_row_the_provider_refused_stops_the_script(tmp_path: Path) -> None:
    run = _run(tmp_path, contacts_script(_PEOPLE), fail_on="phone_v2")

    assert run.status != 0
    assert "Error while accessing provider" in run.stderr
    assert not [call for call in run.calls if "email_v2" in call], "nothing after the refusal"


def test_a_raw_contact_that_did_not_appear_stops_the_script(tmp_path: Path) -> None:
    run = _run(tmp_path, contacts_script(_PEOPLE), grow=0)

    assert run.status != 0
    assert "no raw contact id" in run.stderr
    assert not [call for call in run.calls if "/data " in call]


def test_a_contact_count_short_of_the_list_fails_the_script(tmp_path: Path) -> None:
    lines = contacts_script(_PEOPLE)
    # Drop the second person's raw contact: its rows land on the first one's id.
    second = next(i for i, line in enumerate(lines) if i > 1 and "/raw_contacts --bind" in line)
    del lines[second]

    run = _run(tmp_path, lines)

    assert run.status != 0
    assert "contacts missing" in run.stderr


def test_the_gallery_prep_removes_an_earlier_trys_photos_and_lists_these(
    tmp_path: Path,
) -> None:
    camera, listed = tmp_path / "Camera", tmp_path / "photos"
    camera.mkdir()
    (camera / "20260901_100000.jpg").write_bytes(b"old")
    (camera / "keep.jpg").write_bytes(b"not ours")
    listed.write_text("20260901_100000.jpg\n", encoding="utf-8", newline="\n")
    taken = datetime(2026, 10, 3, 14, 30, 22, tzinfo=UTC)
    photos = [Photo("20261003_143022.jpg", taken, b""), Photo("b.jpg", taken, b"")]
    lines = [
        line.replace(_seeder.CAMERA_DIR, camera.as_posix()).replace(
            _seeder._PHOTO_LIST, listed.as_posix()
        )
        for line in gallery_prep_script(photos)
    ]

    run = _run(tmp_path, lines)

    assert run.status == 0, run.stderr
    assert sorted(p.name for p in camera.iterdir()) == ["keep.jpg"]
    assert listed.read_text(encoding="utf-8").split() == ["20261003_143022.jpg", "b.jpg"]
