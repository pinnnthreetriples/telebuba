"""The start-up look at the credential folders: one journal row per open path, never a path."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core.config import settings
from services.session_guard import _private_paths as check
from tests.private_path_support import open_to_others

if TYPE_CHECKING:
    from pathlib import Path

Logged = list[tuple[str, str, dict[str, Any]]]


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> Logged:
    rows: Logged = []

    async def _log(level: str, event: str, **kwargs: Any) -> None:
        rows.append((level, event, kwargs))

    monkeypatch.setattr(check, "log_event", _log)
    return rows


@pytest.fixture
def mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "second_disk"
    folder.mkdir()
    monkeypatch.setattr(settings.session_vault, "mirror_dir", folder)
    return folder


@pytest.mark.asyncio
async def test_an_open_mirror_is_journalled_once_by_name_without_its_path(
    logged: Logged, mirror: Path
) -> None:
    open_to_others(mirror)

    await check.check_private_paths()

    rows = [row for row in logged if row[2]["extra"]["what"] == "vault_mirror"]
    assert len(rows) == 1
    level, event, kwargs = rows[0]
    assert (level, event) == ("WARNING", "private_path_open")
    assert kwargs["extra"]["grants"]
    assert str(mirror) not in repr(logged)


@pytest.mark.asyncio
async def test_a_check_that_fails_is_journalled_by_error_type_and_never_raises(
    logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom() -> dict[str, list[str]]:
        msg = "secret-path-name"
        raise PermissionError(msg)

    monkeypatch.setattr(check, "_open_by_name", _boom)

    await check.check_private_paths()

    assert logged == [
        ("WARNING", "private_path_check_failed", {"extra": {"error_type": "PermissionError"}})
    ]


@pytest.mark.asyncio
async def test_nothing_is_journalled_when_nothing_is_configured_open(
    logged: Logged, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "_open_by_name", dict)

    await check.check_private_paths()

    assert logged == []
