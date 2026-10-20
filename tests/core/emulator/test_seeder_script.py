"""The seeding's contacts script under a real ``sh``, ``content`` faked as the device's.

Android's ``content`` exits 0 even when the provider refused a row and only prints
the error, so the script must stop on that print and the marker must never follow.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

import pytest

from core.emulator import _seeder
from core.emulator._seed_data import Contact, Photo
from core.emulator._seeder import _SCRIPT_HEAD, contacts_script, gallery_prep_script

if TYPE_CHECKING:
    from pathlib import Path

_SH = shutil.which("sh")
pytestmark = pytest.mark.skipif(_SH is None, reason="needs a POSIX sh")

# A shell function wins over the binary. A raw contact is a line in $ROWS holding its
# ``sync2`` tag, its _id that line's number (appends do not race between the lanes);
# a query for one tag finds that row alone, one for every tagged row lists them all
# but $LOSE (a row the provider lost), and an insert whose argv holds $FAIL_ON prints
# the provider's error and exits 0, as the real tool does. $GROW=0 writes no rows.
_FAKE_CONTENT = """
content() {
  echo "$*" >> "$WORK/calls.log"
  case "$1" in
    insert)
      case "$*" in
        *"$FAIL_ON"*) echo "Error while accessing provider:com.android.contacts" >&2 ;;
        *raw_contacts*)
          [ "$GROW" -eq 0 ] || echo "$*" | sed 's/.*sync2:s:\\([0-9]*\\).*/\\1/' >> "$ROWS" ;;
      esac ;;
    query)
      case "$*" in
        *sync2=*)
          tag=$(echo "$*" | sed "s/.*sync2='\\([0-9]*\\)'.*/\\1/")
          grep -n "^$tag\\$" "$ROWS" | sed 's/:.*//; s/^/Row: 0 _id=/' ;;
        *) grep -vn "^$LOSE\\$" "$ROWS" | sed 's/:.*//; s/^/Row: 0 _id=/' ;;
      esac ;;
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


# More people than lanes, so lanes write several each and their raw contacts interleave.
_CROWD = [
    *_PEOPLE,
    *(Contact(f"P{i}", "Lane", f"0151 0000 {i:03d}", None, None) for i in range(3, 11)),
]


def _run(
    tmp_path: Path,
    lines: list[str],
    *,
    fail_on: str = "never-matches",
    grow: int = 1,
    lose: int = 0,
) -> _Run:
    rows = tmp_path / "rows"
    rows.write_text("", encoding="utf-8")
    script = tmp_path / "seed.sh"
    script.write_text(
        "\n".join((_FAKE_CONTENT, *_SCRIPT_HEAD, *lines)) + "\n", encoding="utf-8", newline="\n"
    )
    env = {
        "PATH": os.environ["PATH"],
        "WORK": tmp_path.as_posix(),
        "ROWS": rows.as_posix(),
        "FAIL_ON": fail_on,
        "GROW": str(grow),
        "LOSE": str(lose),
    }
    assert _SH is not None
    done = subprocess.run(  # noqa: S603 - test-owned argv
        [_SH, script.as_posix()], env=env, check=False, timeout=60, capture_output=True
    )
    log = tmp_path / "calls.log"
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return _Run(done.returncode, calls, done.stderr.decode("utf-8", errors="replace"))


def _data(calls: list[str]) -> list[str]:
    return [
        call
        for call in calls
        if call.startswith("insert --uri content://com.android.contacts/data")
    ]


def test_every_contact_goes_in_under_its_own_raw_contact(tmp_path: Path) -> None:
    run = _run(tmp_path, contacts_script(_CROWD))

    assert run.status == 0, run.stderr
    # Line n of $ROWS is the raw contact with _id n; it holds that contact's place.
    place_of = {
        str(row_id): int(tag)
        for row_id, tag in enumerate((tmp_path / "rows").read_text().split(), start=1)
    }
    assert sorted(place_of.values()) == list(range(1, len(_CROWD) + 1))
    by_id: dict[int, list[str]] = {}
    for call in _data(run.calls):
        row_id = re.search(r"raw_contact_id:i:(\d+) ", call)
        assert row_id is not None, call
        by_id.setdefault(place_of[row_id[1]], []).append(call)
    for place, person in enumerate(_CROWD, start=1):
        rows = by_id[place]
        assert len(rows) == 2 + bool(person.email) + bool(person.company)
        # Quoted for sh, the name reaches ``content`` whole, quote and umlaut included.
        assert f"data1:s:{person.first} {person.last} " in rows[0]
        assert f"data1:s:{person.phone} " in rows[1]


def test_a_refused_row_stops_its_lane_and_fails_the_script_once_all_are_done(
    tmp_path: Path,
) -> None:
    # The first lane writes the 1st and the 9th person; the 1st's phone is refused.
    run = _run(tmp_path, contacts_script(_CROWD), fail_on=_CROWD[0].phone)

    assert run.status != 0
    assert "Error while accessing provider" in run.stderr
    assert "a contact was not written" in run.stderr
    lane = [c for c in run.calls if "j.oneil@gmx.de" in c or re.search(r"sync2:s:9\b", c)]
    assert not lane, "nothing after the refusal in its lane"
    # The other lanes ran to their end: the script never leaves a writer behind.
    written = set(re.findall(r"sync2:s:(\d+)\b", "\n".join(run.calls)))
    assert written == {str(n) for n in range(1, len(_CROWD) + 1)} - {"9"}
    assert not [call for call in run.calls if call.startswith("query") and "sync2" not in call]


def test_a_raw_contact_that_did_not_appear_stops_the_script(tmp_path: Path) -> None:
    run = _run(tmp_path, contacts_script(_PEOPLE), grow=0)

    assert run.status != 0
    assert "no raw contact id" in run.stderr
    assert not _data(run.calls)


def test_a_contact_count_short_of_the_list_fails_the_script(tmp_path: Path) -> None:
    run = _run(tmp_path, contacts_script(_PEOPLE), lose=2)

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
