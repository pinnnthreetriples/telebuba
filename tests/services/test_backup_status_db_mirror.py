"""The backups status reports the encrypted database mirror."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.session_vault import new_vault_key
from services.session_guard._backup_status import read_backup_status

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _no_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.passkey_vault, "token", "")


@pytest.mark.asyncio
async def test_off_without_key_or_mirror() -> None:
    status = await read_backup_status()
    assert status.db_mirror_enabled is False
    assert status.db_mirror_last_at is None


@pytest.mark.asyncio
async def test_on_with_key_and_mirror_dates_the_newest_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.session_vault, "key", new_vault_key())
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    folder = tmp_path / "mirror" / "db"
    folder.mkdir(parents=True)
    (folder / "telebuba-20260101T000000000000Z.db.enc").write_bytes(b"x")
    (folder / "telebuba-20260102T030405000000Z.db.enc").write_bytes(b"x")
    (folder / "telebuba-29990101T000000000000Z.db.enc").write_bytes(b"x")  # the future: ignored

    status = await read_backup_status()

    assert status.db_mirror_enabled is True
    assert status.db_mirror_last_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC).isoformat()
