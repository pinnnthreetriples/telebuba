"""An uploaded ``.session`` is validated and lands atomically; a failed 2FA write is not an error.

Before: any bytes named ``*.session`` became an account, and ``Path.write_bytes`` could leave a
partial file with no row, after which the same name could never be imported again. Real SQLite
files under ``tmp_path``; a refusal leaves the sessions folder exactly as it was.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_account
from schemas.accounts import AccountSessionFileImport
from schemas.session_json import SellerSessionMeta
from services.accounts import AccountActionError, import_account_session
from services.accounts import _uploads as uploads
from tests.session_files import (
    session_file_bytes,
    write_pyrogram_session_file,
    write_session_file,
)

if TYPE_CHECKING:
    from pathlib import Path


def _folder() -> Path:
    return settings.telegram.session_dir


def _names() -> list[str]:
    return sorted(path.name for path in _folder().glob("*")) if _folder().exists() else []


def _upload(content: bytes, name: str = "acc.session") -> AccountSessionFileImport:
    return AccountSessionFileImport(filename=name, content=content)


def _truncated() -> bytes:
    return session_file_bytes()[:150]


def _pyrogram_bytes(tmp_path: Path) -> bytes:
    return write_pyrogram_session_file(tmp_path / "pyro.session").read_bytes()


def _keyless_bytes(tmp_path: Path) -> bytes:
    return write_session_file(tmp_path / "keyless.session", key=b"").read_bytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["garbage", "truncated", "pyrogram", "empty_key", "no_key"])
async def test_an_invalid_session_upload_is_refused_with_one_code_and_leaves_nothing(
    tmp_path: Path, kind: str
) -> None:
    content = {
        "garbage": b"not a database at all" * 30,
        "truncated": _truncated(),
        "pyrogram": _pyrogram_bytes(tmp_path),
        "empty_key": _keyless_bytes(tmp_path),
        "no_key": session_file_bytes(key=None),
    }[kind]

    with pytest.raises(AccountActionError) as refused:
        await import_account_session(_upload(content))

    assert str(refused.value) == "session_file_invalid"
    assert _names() == []  # not the file, not the temporary name
    assert await fetch_account("acc") is None


@pytest.mark.asyncio
async def test_a_refused_upload_can_be_replaced_by_a_good_one_under_the_same_name() -> None:
    with pytest.raises(AccountActionError):
        await import_account_session(_upload(b"junk" * 40))

    account = await import_account_session(_upload(session_file_bytes()))

    assert account.account_id == "acc"
    assert _names() == ["acc.session"]


@pytest.mark.asyncio
async def test_a_valid_upload_lands_byte_for_byte() -> None:
    content = session_file_bytes()

    await import_account_session(_upload(content))

    assert (_folder() / "acc.session").read_bytes() == content
    assert _names() == ["acc.session"]


@pytest.mark.asyncio
async def test_the_file_is_validated_under_a_temporary_name_then_renamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final name never holds an unvalidated or half-written file."""
    seen: list[tuple[str, bool]] = []
    real = uploads.inspect_session_file

    def watching(path: Path) -> str:
        seen.append((path.name, (_folder() / "acc.session").exists()))
        return real(path)

    monkeypatch.setattr(uploads, "inspect_session_file", watching)

    await import_account_session(_upload(session_file_bytes()))

    [(name, final_existed)] = seen
    assert name != "acc.session"
    assert not name.endswith(".session")  # never mistaken for an account's file
    assert final_existed is False


@pytest.mark.asyncio
async def test_a_leftover_temporary_file_from_a_crashed_import_does_not_block() -> None:
    _folder().mkdir(parents=True, exist_ok=True)
    leftover = _folder() / f"acc.session{uploads.STAGING_SUFFIX}"
    leftover.write_bytes(b"half a file from a crash")

    account = await import_account_session(_upload(session_file_bytes()))

    assert account.account_id == "acc"
    assert _names() == ["acc.session"]


@pytest.mark.asyncio
async def test_an_existing_file_or_account_is_still_refused_and_untouched() -> None:
    content = session_file_bytes()
    await import_account_session(_upload(content))

    with pytest.raises(Exception, match="already exists"):
        await import_account_session(_upload(session_file_bytes()))

    assert (_folder() / "acc.session").read_bytes() == content


@pytest.mark.asyncio
async def test_a_failed_password_write_does_not_fail_the_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[tuple[str, str, str | None, dict[str, object] | None]] = []

    async def record(
        level: str,
        event: str,
        *,
        account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        logged.append((level, event, account_id, extra))

    async def locked(_account_id: str, _password: str) -> None:
        msg = "database is locked"
        raise sqlite3.OperationalError(msg)

    monkeypatch.setattr("services.accounts.sessions.set_account_twofa_pending", locked)
    monkeypatch.setattr("services.accounts.sessions.log_event", record)
    data = AccountSessionFileImport(
        filename="acc.session",
        content=session_file_bytes(),
        meta=SellerSessionMeta(password="fake-pass-1234567"),
    )

    account = await import_account_session(data)

    assert account.account_id == "acc"
    assert await fetch_account("acc") is not None
    assert (_folder() / "acc.session").exists()
    assert ("ERROR", "account_twofa_store_failed", "acc", None) in logged
    assert "fake-pass-1234567" not in repr(logged)
