"""A discarded import goes to the quarantine, never to the bin (``docs/session-vault.md``)."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import fetch_account
from schemas.accounts import AccountSessionFileImport
from services.accounts import import_account_session
from services.accounts._import_rollback import discard_imported_session
from tests.session_files import session_file_bytes, write_session_file

_FINGERPRINT_SEAM = "services.accounts.lifecycle.get_or_create_device_fingerprint"


def _quarantined() -> list[str]:
    folder = settings.session_vault.quarantine_dir
    return (
        sorted(path.name.partition("-")[2] for path in folder.glob("*")) if folder.exists() else []
    )


@pytest.mark.asyncio
async def test_a_failed_import_keeps_the_uploaded_bytes_in_the_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(_account_id: str, _device_type: str = "desktop") -> None:
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr(_FINGERPRINT_SEAM, boom)
    content = session_file_bytes()

    with pytest.raises(RuntimeError, match="database is locked"):
        await import_account_session(
            AccountSessionFileImport(filename="live.session", content=content)
        )

    assert await fetch_account("live") is None
    assert not (settings.telegram.session_dir / "live.session").exists()
    [kept] = settings.session_vault.quarantine_dir.glob("*-live.session")
    assert kept.read_bytes() == content  # moved, not deleted


@pytest.mark.asyncio
async def test_the_rollback_quarantines_a_file_and_drops_its_journal_sidecar() -> None:
    folder = settings.telegram.session_dir
    file = write_session_file(folder / "acc.session")
    journal = folder / "acc.session-journal"
    journal.write_bytes(b"sidecar")

    result = await discard_imported_session("acc", file)

    assert result.outcome == "clean"
    assert _quarantined() == ["acc.session"]
    assert not file.exists()
    assert not journal.exists()


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_moved_is_file_kept_and_stays_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = write_session_file(settings.telegram.session_dir / "acc.session")

    def refuse(_path: object) -> None:
        raise PermissionError(32, "file in use")

    monkeypatch.setattr("core.telegram_client._auth.quarantine_file", refuse)

    result = await discard_imported_session("acc", file)

    assert (result.outcome, result.error_type) == ("file_kept", "PermissionError")
    assert file.exists()
