"""``python -m tools.secure_paths``: what it reports, and that ``fix`` touches only open paths."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from core import private_paths, secure_paths
from tests.private_path_support import open_to_others
from tools import secure_paths as cli

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    named = {
        "sessions": tmp_path / "sessions",
        "database": tmp_path / "telebuba.db",
        "vault_mirror": tmp_path / "mirror",
        "backups": tmp_path / "missing",
    }
    named["sessions"].mkdir()
    named["vault_mirror"].mkdir()
    named["database"].write_bytes(b"x")
    monkeypatch.setattr(private_paths, "configured_private_paths", lambda: named)
    monkeypatch.setattr(cli, "configured_private_paths", lambda: named)
    for path in (named["sessions"], named["database"], named["vault_mirror"]):
        secure_paths.restrict_private_path(path)
    return named


@pytest.mark.usefixtures("folders")
def test_check_passes_when_every_existing_path_is_private(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["check"]) == 0

    out = capsys.readouterr().out
    assert "sessions: private" in out
    assert "backups: missing" in out
    assert "open: 0 of 4" in out


def test_check_names_the_open_path_and_exits_1(
    folders: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    open_to_others(folders["vault_mirror"])

    assert cli.main(["check"]) == 1

    out = capsys.readouterr().out
    assert "vault_mirror: open to" in out
    assert "sessions: private" in out


def test_fix_narrows_only_the_open_path_and_checks_again(
    folders: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_to_others(folders["vault_mirror"])
    narrowed: list[Path] = []
    real = secure_paths.restrict_private_path
    monkeypatch.setattr(
        cli, "restrict_private_path", lambda path: (narrowed.append(path), real(path))[1]
    )

    assert cli.main(["fix"]) == 0

    out = capsys.readouterr().out
    assert narrowed == [folders["vault_mirror"]]
    assert "vault_mirror: narrowed to the owner" in out
    assert "open: 0 of 4" in out
    assert secure_paths.open_private_paths(folders.values()) == []


def test_fix_reports_a_failure_and_exits_1(
    folders: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_to_others(folders["sessions"])

    def _refuse(_path: Path) -> None:
        msg = "denied"
        raise PermissionError(msg)

    monkeypatch.setattr(cli, "restrict_private_path", _refuse)

    assert cli.main(["fix"]) == 1

    assert "sessions: not changed (PermissionError" in capsys.readouterr().err


def test_open_private_paths_skips_missing_paths_and_lists_the_open_ones(
    folders: dict[str, Path],
) -> None:
    open_to_others(folders["database"])

    found = secure_paths.open_private_paths(folders.values())

    assert [path for path, _grants in found] == [folders["database"]]
    assert found[0][1]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_on_posix_a_group_or_other_mode_is_the_open_grant(tmp_path: Path) -> None:
    folder = tmp_path / "keys"
    folder.mkdir()
    folder.chmod(0o750)

    assert secure_paths.open_private_paths([folder]) == [(folder, ["group_or_other"])]

    secure_paths.restrict_private_path(folder)

    assert secure_paths.open_private_paths([folder]) == []
