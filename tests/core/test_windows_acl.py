"""NTFS access lists: the SDDL reader on literal strings, and the real thing on Windows."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from core import _windows_acl as acl

if TYPE_CHECKING:
    from pathlib import Path

_WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="NTFS access lists")

# The default list of a data disk's root, as ``icacls`` would show it: Users read, and
# Authenticated Users able to create and change (inherit-only for the files inside).
_DISK_ROOT = (
    "O:BAG:SYD:PAI(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICIIO;GA;;;CO)"
    "(A;OICI;0x1200a9;;;BU)(A;CI;LC;;;BU)(A;CIIO;DC;;;BU)(A;;0x1301bf;;;AU)"
)
_OWNER_ONLY = "O:BAG:SYD:PAI(A;OICI;FA;;;BA)(A;OICI;FA;;;SY)(A;OICI;FA;;;S-1-5-21-1-2-3-1001)"


def test_a_data_disk_root_grants_users_and_authenticated_users() -> None:
    assert acl.broad_grants_in_sddl(_DISK_ROOT) == ["users", "authenticated_users"]


def test_an_owner_only_list_has_no_broad_grant() -> None:
    assert acl.broad_grants_in_sddl(_OWNER_ONLY) == []


@pytest.mark.parametrize(
    ("ace", "expected"),
    [
        ("(A;;FA;;;WD)", ["everyone"]),
        ("(A;;FR;;;S-1-1-0)", ["everyone"]),
        ("(A;OICI;GR;;;IU)", ["interactive"]),
        ("(A;;FR;;;AN)", ["anonymous"]),
        ("(A;;FR;;;BG)", ["guests"]),
        ("(A;;FR;;;S-1-5-32-545)", ["users"]),
        # Not grants: a denial, an entry only handed down to children, one with no rights.
        ("(D;;FA;;;WD)", []),
        ("(A;OICIIO;FA;;;WD)", []),
        ("(A;;;;;WD)", []),
        ("(A;;0x0;;;WD)", []),
        # Inherited from the parent still counts: it is the object's own effective access.
        ("(A;ID;FR;;;AU)", ["authenticated_users"]),
        # Domain Users counts as broad; other principals are out of scope.
        ("(A;;FA;;;S-1-5-21-1-2-3-513)", ["domain_users"]),
        ("(A;;FA;;;S-1-5-21-1-2-3-1001)", []),
    ],
)
def test_ace_rules(ace: str, expected: list[str]) -> None:
    assert acl.broad_grants_in_sddl(f"O:BAG:SYD:PAI{ace}") == expected


def test_each_principal_is_reported_once_and_the_sacl_is_ignored() -> None:
    sddl = "D:(A;;FR;;;WD)(A;;FW;;;S-1-1-0)S:AI(A;;FA;;;AU)"
    assert acl.broad_grants_in_sddl(sddl) == ["everyone"]


def test_an_empty_string_has_no_grant_and_a_dacl_less_one_is_open() -> None:
    assert acl.broad_grants_in_sddl("") == []
    # No DACL part at all is a NULL DACL, which is open to everyone.
    assert acl.broad_grants_in_sddl("O:BAG:SY") == ["everyone"]


@pytest.mark.skipif(sys.platform == "win32", reason="the guard is for other systems")
def test_reading_a_list_off_windows_is_refused(tmp_path: Path) -> None:
    with pytest.raises(acl.AclError):
        acl.broad_grants(tmp_path)
    with pytest.raises(acl.AclError):
        acl.restrict_to_owner(tmp_path)


def _icacls_grant(path: Path, grant: str) -> None:
    subprocess.run(  # noqa: S603 - a fixed tool and a temp path, no shell
        ["icacls", str(path), "/grant", grant],  # noqa: S607
        check=True,
        capture_output=True,
    )


@_WINDOWS_ONLY
def test_the_real_reader_reports_a_grant_made_to_authenticated_users(tmp_path: Path) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    _icacls_grant(folder, "*S-1-5-11:(OI)(CI)R")

    assert "authenticated_users" in acl.broad_grants(folder)


@_WINDOWS_ONLY
def test_restrict_to_owner_removes_the_broad_grants_and_keeps_our_access(tmp_path: Path) -> None:
    folder = tmp_path / "private"
    folder.mkdir()
    existing = folder / "old.session"
    existing.write_bytes(b"x")
    _icacls_grant(folder, "*S-1-1-0:(OI)(CI)R")
    assert "everyone" in acl.broad_grants(folder)

    acl.restrict_to_owner(folder)

    assert acl.broad_grants(folder) == []
    assert acl.broad_grants(existing) == []
    created = folder / "new.session"
    created.write_bytes(b"y")
    assert created.read_bytes() == b"y"
    assert existing.read_bytes() == b"x"
    assert acl.broad_grants(created) == []


@_WINDOWS_ONLY
def test_restrict_to_owner_on_a_file(tmp_path: Path) -> None:
    target = tmp_path / "telebuba.db"
    target.write_bytes(b"x")
    _icacls_grant(target, "*S-1-5-11:R")

    acl.restrict_to_owner(target)

    assert acl.broad_grants(target) == []
    assert target.read_bytes() == b"x"


@_WINDOWS_ONLY
def test_the_current_user_sid_is_a_domain_sid() -> None:
    assert acl.current_user_sid().startswith("S-1-5-")


@_WINDOWS_ONLY
def test_a_failing_icacls_raises_with_its_exit_code_and_no_path(tmp_path: Path) -> None:
    missing = tmp_path / "secret-folder-name" / "gone"

    with pytest.raises(acl.AclError) as raised:
        acl.restrict_to_owner(missing)

    assert raised.value.code not in (None, 0)
    assert "secret-folder-name" not in str(raised.value)


@_WINDOWS_ONLY
def test_reading_a_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(acl.AclError) as raised:
        acl.broad_grants(tmp_path / "nope")

    assert raised.value.code
