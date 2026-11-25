"""Adversarial checks of the Windows private-path code, on real temp directories (Windows only).

Every ACL change here lands on a directory under ``tmp_path`` that is deleted afterwards.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from core import _windows_acl as acl
from core.secure_paths import open_private_paths, restrict_private_path

if TYPE_CHECKING:
    from pathlib import Path

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="NTFS access lists")

_EVERYONE = "*S-1-1-0"
_AUTH_USERS = "*S-1-5-11"
_USERS = "*S-1-5-32-545"


def _icacls(*args: str) -> None:
    done = subprocess.run(  # noqa: S603
        [acl._icacls(), *args], check=False, capture_output=True, timeout=60
    )
    assert done.returncode == 0, done.stdout


def _grant(path: Path, sid: str, right: str = "(OI)(CI)R") -> None:
    _icacls(str(path), "/grant", f"{sid}:{right}")


# ------------------------------------------------------------------------ pure SDDL


@pytest.mark.parametrize(
    ("sddl", "expected"),
    [
        ("D:PAI(A;OICI;FA;;;WD)", ["everyone"]),
        ("D:PAI(A;OICI;FA;;;S-1-1-0)", ["everyone"]),
        ("D:PAI(A;OICI;FA;;;s-1-1-0)", ["everyone"]),
        ("D:PAI(A;;FA;;;AU)(A;;FA;;;BU)", ["authenticated_users", "users"]),
        ("D:PAI(A;;0x1200a9;;;WD)", ["everyone"]),
        ("D:PAI(A;;0X1200A9;;;WD)", ["everyone"]),
        ("D:PAI(A;;GR;;;WD)", ["everyone"]),
        ("D:PAI(A;CIIO;FA;;;WD)", []),
        ("D:PAI(A;OICIIOID;FA;;;WD)", []),
        ("D:PAI(D;;FA;;;WD)", []),
        ("D:PAI(A;;0x0;;;WD)", []),
        ("D:PAI(A;;;;;WD)", []),
        ("D:PAI(A;;FA;;;SY)(A;;FA;;;BA)", []),
        ("D:P", []),
        ("D:", []),
        ("", []),
        ("D:PAI(A;;FA;;;BA)S:(AU;SAFA;FA;;;WD)", []),
        ("D:PAI(A;;FA;;;BA)S:AI(A;;FA;;;WD)", []),
        ("D:PAI(A;ID;FA;;;WD)", ["everyone"]),
        ("D:PAI(A;OICINP;FA;;;WD)", ["everyone"]),
        ("D:PAI(A;;FA;;;WD)(A;;FA;;;WD)", ["everyone"]),
        ("D:PAI(A;;FA;;;IU)(A;;FA;;;AN)(A;;FA;;;BG)", ["interactive", "anonymous", "guests"]),
    ],
)
def test_sddl_parsing(sddl: str, expected: list[str]) -> None:
    assert acl.broad_grants_in_sddl(sddl) == expected


def test_null_dacl_is_reported_as_open_to_everyone() -> None:
    assert acl.broad_grants_in_sddl("D:NO_ACCESS_CONTROL") == ["everyone"]


def test_conditional_allow_ace_for_everyone_is_reported() -> None:
    sddl = 'D:PAI(XA;;FA;;;WD;(@User.Title=="PM"))'
    assert acl.broad_grants_in_sddl(sddl) == ["everyone"]


def test_domain_users_and_network_are_reported() -> None:
    assert acl.broad_grants_in_sddl("D:PAI(A;;FA;;;S-1-5-21-1-2-3-513)(A;;FA;;;NU)")


# --------------------------------------------------------------------------- real ACLs


@windows_only
def test_current_user_sid_matches_whoami() -> None:
    out = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert acl.current_user_sid() in out


@windows_only
def test_fresh_temp_dir_is_private_baseline(tmp_path: Path) -> None:
    target = tmp_path / "base"
    target.mkdir()
    # tmp_path inherits from the user's profile: no broad principal expected.
    assert acl.broad_grants(target) == []


@windows_only
@pytest.mark.parametrize(
    ("sid", "code"),
    [(_EVERYONE, "everyone"), (_AUTH_USERS, "authenticated_users"), (_USERS, "users")],
)
def test_real_dacl_reports_each_broad_principal_for_dir_and_file(
    tmp_path: Path, sid: str, code: str
) -> None:
    folder = tmp_path / "dir"
    folder.mkdir()
    _grant(folder, sid)
    assert acl.broad_grants(folder) == [code]
    child = folder / "child.txt"
    child.write_text("x")
    assert acl.broad_grants(child) == [code]  # inherited
    lone = tmp_path / "lone.txt"
    lone.write_text("x")
    _grant(lone, sid, "R")
    assert acl.broad_grants(lone) == [code]


@windows_only
def test_unicode_and_spaced_paths(tmp_path: Path) -> None:
    folder = tmp_path / "сессии 日本 (копия) & co"
    folder.mkdir()
    _grant(folder, _EVERYONE)
    assert open_private_paths([folder]) == [(folder, ["everyone"])]
    restrict_private_path(folder)
    assert open_private_paths([folder]) == []


@windows_only
def test_restrict_removes_all_broad_keeps_owner_and_children_inherit(tmp_path: Path) -> None:
    folder = tmp_path / "vault"
    folder.mkdir()
    for sid in (_EVERYONE, _AUTH_USERS, _USERS):
        _grant(folder, sid)
    old_child = folder / "old.bin"
    old_child.write_bytes(b"1")
    assert set(acl.broad_grants(folder)) == {"everyone", "authenticated_users", "users"}
    restrict_private_path(folder)
    assert acl.broad_grants(folder) == []
    assert acl.broad_grants(old_child) == []
    # the running user is never locked out: write, read, list, delete
    (folder / "new.bin").write_bytes(b"2")
    assert (folder / "new.bin").read_bytes() == b"2"
    assert sorted(p.name for p in folder.iterdir()) == ["new.bin", "old.bin"]
    sddl = acl.read_sddl(folder)
    assert acl.current_user_sid() in sddl or "OW" in sddl or "FA" in sddl
    (folder / "new.bin").unlink()
    acl.broad_grants(folder / "old.bin")


@windows_only
def test_restrict_on_a_file(tmp_path: Path) -> None:
    target = tmp_path / "telebuba.db"
    target.write_bytes(b"db")
    _grant(target, _EVERYONE, "R")
    restrict_private_path(target)
    assert acl.broad_grants(target) == []
    assert target.read_bytes() == b"db"
    target.write_bytes(b"db2")


@windows_only
def test_restrict_does_not_touch_siblings_or_parent(tmp_path: Path) -> None:
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    _grant(sibling, _EVERYONE)
    target = tmp_path / "target"
    target.mkdir()
    _grant(target, _EVERYONE)
    before_parent = acl.read_sddl(tmp_path)
    before_sibling = acl.read_sddl(sibling)
    restrict_private_path(target)
    assert acl.read_sddl(tmp_path) == before_parent
    assert acl.read_sddl(sibling) == before_sibling


@windows_only
def test_path_under_a_junction_is_read_through_it(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    _grant(real, _EVERYONE)
    link = tmp_path / "link"
    done = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", str(link), str(real)],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip("junction not creatable")
    assert acl.broad_grants(link) == ["everyone"]


@windows_only
def test_long_path_over_260_chars_is_readable_or_fails_loudly(tmp_path: Path) -> None:
    folder = tmp_path
    for _ in range(6):
        folder = folder / ("d" * 60)
    try:
        folder.mkdir(parents=True)
    except OSError:
        pytest.skip("long paths unavailable")
    _grant(folder, _EVERYONE)
    failure: acl.AclError | None = None
    grants: object = None
    try:
        grants = open_private_paths([folder])
    except acl.AclError as exc:
        failure = exc
    if failure is not None:
        assert failure.code is not None
        assert str(folder) not in str(failure)
        return
    assert grants == [(folder, ["everyone"])]


@windows_only
def test_null_dacl_real_file_is_open_to_everyone(tmp_path: Path) -> None:
    target = tmp_path / "null.txt"
    target.write_text("x")
    advapi32 = acl._advapi32()
    protected = 0x80000000
    advapi32.SetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.SetNamedSecurityInfoW.restype = ctypes.c_ulong
    status = advapi32.SetNamedSecurityInfoW(str(target), 1, 4 | protected, None, None, None, None)
    if status != 0:
        pytest.skip(f"NULL DACL not settable ({status})")
    assert acl.broad_grants(target) == ["everyone"]


# ----------------------------------------------------------- CLI and start-up check


@pytest.fixture
def fx_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    from services.session_guard import _private_paths  # noqa: PLC0415
    from tools import secure_paths  # noqa: PLC0415

    open_dir = tmp_path / "open"
    closed_dir = tmp_path / "closed"
    outside = tmp_path / "outside"
    for folder in (open_dir, closed_dir, outside):
        folder.mkdir()
    _grant(open_dir, _EVERYONE)
    _grant(outside, _EVERYONE)
    mapping = {"sessions": open_dir, "backups": closed_dir, "ghost": tmp_path / "missing"}
    monkeypatch.setattr(secure_paths, "configured_private_paths", lambda: mapping)
    monkeypatch.setattr(_private_paths, "configured_private_paths", lambda: mapping)
    return {"open": open_dir, "closed": closed_dir, "outside": outside}


@windows_only
def test_check_exit_code_and_fix_touch_only_open_configured_paths(
    fx_paths: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from tools import secure_paths  # noqa: PLC0415

    closed_before = acl.read_sddl(fx_paths["closed"])
    assert secure_paths.main(["check"]) == 1
    assert secure_paths.main(["fix"]) == 0
    assert acl.broad_grants(fx_paths["open"]) == []
    assert acl.read_sddl(fx_paths["closed"]) == closed_before
    assert acl.broad_grants(fx_paths["outside"]) == ["everyone"]
    assert secure_paths.main(["check"]) == 0
    assert "missing" in capsys.readouterr().out


@windows_only
def test_fix_with_nothing_open_changes_nothing_and_exits_zero(
    fx_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools import secure_paths  # noqa: PLC0415

    restrict_private_path(fx_paths["open"])
    before = {name: acl.read_sddl(path) for name, path in fx_paths.items() if name != "outside"}
    calls: list[Path] = []
    monkeypatch.setattr(secure_paths, "restrict_private_path", calls.append)
    assert secure_paths.main(["fix"]) == 0
    assert calls == []
    assert before == {name: acl.read_sddl(fx_paths[name]) for name in before}


@windows_only
@pytest.mark.usefixtures("fx_paths")
def test_fix_reports_failure_with_exit_one_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tools import secure_paths  # noqa: PLC0415

    def boom(_path: Path) -> None:
        message = "icacls failed with exit code 5"
        raise acl.AclError(message, code=5)

    monkeypatch.setattr(secure_paths, "restrict_private_path", boom)
    assert secure_paths.main(["fix"]) == 1
    assert "not changed" in capsys.readouterr().err


@windows_only
@pytest.mark.asyncio
async def test_startup_check_journals_stable_names_never_paths_and_never_raises(
    fx_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.session_guard import _private_paths  # noqa: PLC0415

    rows: list[tuple[str, str, dict[str, object]]] = []

    async def fake_log(level: str, event: str, **kwargs: object) -> None:
        extra = kwargs.get("extra")
        copied: dict[str, object] = {}
        if isinstance(extra, dict):
            copied.update({str(name): value for name, value in extra.items()})
        rows.append((level, event, copied))

    monkeypatch.setattr(_private_paths, "log_event", fake_log)
    await _private_paths.check_private_paths()
    assert rows == [("WARNING", "private_path_open", {"what": "sessions", "grants": ["everyone"]})]
    assert str(fx_paths["open"]) not in repr(rows)


@windows_only
@pytest.mark.asyncio
async def test_one_unreadable_path_does_not_hide_the_other_open_ones(
    fx_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import secure_paths as core_secure  # noqa: PLC0415
    from services.session_guard import _private_paths  # noqa: PLC0415

    real = core_secure._windows_acl.broad_grants
    bad = fx_paths["closed"]

    def flaky(path: Path) -> list[str]:
        if path == bad:
            message = "denied"
            raise acl.AclError(message, code=5)
        return real(path)

    mapping = {"backups": bad, "sessions": fx_paths["open"]}
    monkeypatch.setattr(_private_paths, "configured_private_paths", lambda: mapping)
    monkeypatch.setattr(core_secure._windows_acl, "broad_grants", flaky)
    events: list[str] = []

    async def fake_log(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr(_private_paths, "log_event", fake_log)
    await _private_paths.check_private_paths()
    assert "private_path_open" in events
