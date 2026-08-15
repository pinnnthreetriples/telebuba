"""Account session and tdata import service tests."""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import TYPE_CHECKING

import pytest

from core import secure_paths
from core.config import settings
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountRead,
    AccountSessionFileImport,
    health_for_status,
)
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import (
    SessionAlreadyExistsError,
    account_stats,
    add_account,
    check_account_session,
    import_account_session,
    import_account_tdata,
    list_accounts_page,
    remove_account,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_import_account_session_saves_file_and_creates_account() -> None:
    account = await import_account_session(
        AccountSessionFileImport(
            filename="real-account.session",
            content=b"sqlite session bytes",
            label="Real account",
        ),
    )
    page = await list_accounts_page()

    assert account.account_id == "real-account"
    assert account.session_name == "real-account"
    assert (
        settings.telegram.session_dir / "real-account.session"
    ).read_bytes() == b"sqlite session bytes"
    row = page.items[0]
    assert row.label == "Real account"
    assert row.session_name == "real-account"


@pytest.mark.asyncio
async def test_concurrent_identical_session_imports_serialize() -> None:
    """Two same-name imports racing must not both pass the existence check.

    The per-key import lock serializes check→write→add, so exactly one import
    wins and the other observes the now-existing account and raises
    SessionAlreadyExistsError — the winner's credential is never overwritten.
    Without the lock both would pass the check and the second would clobber the
    first's .session (or trip a DB integrity error, not the domain error).
    """
    results = await asyncio.gather(
        import_account_session(
            AccountSessionFileImport(filename="race.session", content=b"first", label="A"),
        ),
        import_account_session(
            AccountSessionFileImport(filename="race.session", content=b"second", label="B"),
        ),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    errors = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], SessionAlreadyExistsError)

    # Exactly one account row, and the stored credential is the winner's — the
    # loser never overwrote it.
    page = await list_accounts_page()
    assert len(page.items) == 1
    winner = successes[0]
    expected = b"first" if winner.label == "A" else b"second"
    assert (settings.telegram.session_dir / "race.session").read_bytes() == expected


@pytest.mark.asyncio
async def test_import_account_session_rejects_non_session_file() -> None:
    with pytest.raises(ValueError, match=r"\.session"):
        await import_account_session(
            AccountSessionFileImport(filename="not-session.txt", content=b"content"),
        )


@pytest.mark.asyncio
async def test_import_account_session_rejected_id_leaves_no_file() -> None:
    """A refused id must not leave the credential on disk.

    ``_session_filename`` only checks the suffix and a non-empty stem, and
    ``Path("..session").stem`` is ``"."`` — so the bytes were written and only then
    rejected by ``AccountCreate``'s charset pattern, orphaning them in
    ``session_dir`` with no account row to ever delete them.
    """
    with pytest.raises(ValueError, match="account_id"):
        await import_account_session(
            AccountSessionFileImport(filename="..session", content=b"credential-bytes"),
        )

    assert not (settings.telegram.session_dir / "..session").exists()
    assert list((settings.telegram.session_dir).glob("*")) == []


@pytest.mark.asyncio
async def test_session_import_locks_down_the_directory_and_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dir must end up 0700 and the ``.session`` 0600.

    A 0600 file inside a 0755 directory still lists its name and size to every local
    account, and ``sessions/`` was being created at the default umask. Asserted by
    the modes REQUESTED so it holds on a Windows checkout too, where ``os.chmod``
    only toggles the read-only bit — see ``core.secure_paths``.
    """
    modes: dict[str, int] = {}
    monkeypatch.setattr(secure_paths, "_IS_POSIX", True)
    monkeypatch.setattr(
        "pathlib.Path.chmod",
        lambda self, mode: modes.__setitem__(self.name, mode),
    )
    await import_account_session(
        AccountSessionFileImport(filename="perm.session", content=b"credential-bytes"),
    )
    assert modes[settings.telegram.session_dir.name] == 0o700
    assert modes["perm.session"] == 0o600


@pytest.mark.asyncio
async def test_failed_import_cleans_up_so_the_retry_can_succeed() -> None:
    """An import that fails AFTER the write must not block every later attempt.

    ``session_name`` can be taken by a different ``account_id`` whose own file is
    gone from disk, so neither the DB check nor the file check sees the conflict and
    ``add_account`` refuses only after the credential has landed. The orphan then
    made ``session_path.exists()`` refuse every retry forever, with no account row
    for the operator to delete — a retryable failure turned permanent.
    """
    await add_account(AccountCreate(account_id="other", label="Other", session_name="123"))
    data = AccountSessionFileImport(filename="123.session", content=b"credential-bytes")
    orphan = settings.telegram.session_dir / "123.session"

    with pytest.raises(ValueError, match="already used by account"):
        await import_account_session(data)
    assert not orphan.exists()

    # With the conflicting row gone the very same import must now go through.
    await remove_account("other")
    account = await import_account_session(data)
    assert account.account_id == "123"
    assert orphan.read_bytes() == b"credential-bytes"


@pytest.mark.asyncio
async def test_a_failed_cleanup_is_reported_and_leaves_the_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlink that itself fails must not mask why the import failed."""
    await add_account(AccountCreate(account_id="other", label="Other", session_name="123"))

    def _unlink(_self: Path, *, missing_ok: bool = False) -> None:  # noqa: ARG001 - mirrors Path.unlink
        raise PermissionError(32, "file in use")

    monkeypatch.setattr("pathlib.Path.unlink", _unlink)
    with pytest.raises(ValueError, match="already used by account"):
        await import_account_session(
            AccountSessionFileImport(filename="123.session", content=b"credential-bytes"),
        )


@pytest.mark.asyncio
async def test_import_account_session_refuses_to_overwrite_existing() -> None:
    """Re-uploading a same-named session must NOT silently replace credentials."""
    await import_account_session(
        AccountSessionFileImport(
            filename="dup.session",
            content=b"original-session-bytes",
            label="Original",
        ),
    )
    session_path = settings.telegram.session_dir / "dup.session"
    assert session_path.read_bytes() == b"original-session-bytes"

    with pytest.raises(SessionAlreadyExistsError):
        await import_account_session(
            AccountSessionFileImport(
                filename="dup.session",
                content=b"attacker-session-bytes",
                label="Replacement",
            ),
        )
    # File must be untouched.
    assert session_path.read_bytes() == b"original-session-bytes"


@pytest.mark.asyncio
async def test_import_account_tdata_registers_each_account_and_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(
                    user_id=111,
                    session_path=str(settings.telegram.session_dir / "111.session"),
                ),
                TdataAccountSummary(
                    user_id=222,
                    session_path=str(settings.telegram.session_dir / "222.session"),
                ),
            ],
        )

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        request_account_id = getattr(_request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=request_account_id,
            session_path=f"sessions/{request_account_id}",
            status="alive",
            is_temporary=False,
            user_id=int(request_account_id),
            username=f"user{request_account_id}",
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)

    result = await asyncio.wait_for(
        import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x", label="My pool"),
        ),
        timeout=2.0,
    )

    assert [a.account_id for a in result.accounts] == ["111", "222"]
    assert all(a.status == "alive" for a in result.accounts)


@pytest.mark.asyncio
async def test_distinct_tdata_imports_make_progress_independently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An import for one account must not block an unrelated account.

    The lock protects a credential/account identity, not the whole tdata
    subsystem. Both imports pause at the first externally visible write until
    the other reaches the same point. A global lock would deadlock this
    rendezvous; correctly partitioned locks let both operations complete.
    """
    entered = {"111": asyncio.Event(), "222": asyncio.Event()}

    async def fake_convert(
        request: TdataConvertRequest,
        _staging_dir: object,
    ) -> TdataConvertResult:
        account_id = request.filename.removesuffix(".zip")
        session_path = tmp_path / f"{account_id}.session"
        session_path.write_bytes(f"session-{account_id}".encode())
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(
                    user_id=int(account_id),
                    session_path=str(session_path),
                ),
            ],
        )

    async def rendezvous_add(account: AccountCreate) -> None:
        entered[account.account_id].set()
        other_id = "222" if account.account_id == "111" else "111"
        await entered[other_id].wait()

    async def fake_check(request: AccountCheckRequest) -> AccountRead:
        return AccountRead(
            account_id=request.account_id,
            session_name=request.account_id,
            status="alive",
            user_id=int(request.account_id),
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

    async def missing_account(_account_id: str) -> None:
        return None

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.add_account", rendezvous_add)
    monkeypatch.setattr("services.accounts.sessions.check_account_session", fake_check)
    monkeypatch.setattr("services.accounts._tdata.fetch_account", missing_account)

    first, second = await asyncio.wait_for(
        asyncio.gather(
            import_account_tdata(TdataConvertRequest(filename="111.zip", content=b"x")),
            import_account_tdata(TdataConvertRequest(filename="222.zip", content=b"x")),
        ),
        timeout=2.0,
    )

    assert [account.account_id for account in first.accounts] == ["111"]
    assert [account.account_id for account in second.accounts] == ["222"]
    assert all(event.is_set() for event in entered.values())


@pytest.mark.asyncio
async def test_import_account_tdata_surfaces_conversion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(status="invalid_zip", error="bad header")

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)

    with pytest.raises(ValueError, match=r"invalid_zip"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )


@pytest.mark.asyncio
async def test_import_account_tdata_failure_message_is_the_status_code_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The raised message must carry the stable status code and nothing else.

    ``service_errors_to_http`` renders this ``ValueError`` as the HTTP 400
    ``message`` verbatim, and the converter's ``error`` is third-party prose:
    opentele2 stringifies from the raising frame's parameter values, so a real
    failure arrived with the tdata staging path and the proxy URL — credentials
    included — in the response body (non-negotiable #12).

    Pre-fix the message was ``f"{msg} — {result.error}"``, so all three negative
    assertions failed.
    """
    leak = (
        "OpenTeleException: failed to decrypt "
        "C:/Users/op/tdata_staging_x9/tdata/key_datas "
        "via socks5://bob:hunter2@10.20.30.40:1080"
    )

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(status="conversion_error", error=leak)

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)

    with pytest.raises(ValueError) as caught:  # noqa: PT011 - the message IS the assertion.
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )

    assert str(caught.value) == "tdata import failed: conversion_error"
    assert "hunter2" not in str(caught.value)
    assert "tdata_staging_x9" not in str(caught.value)


@pytest.mark.asyncio
async def test_import_account_tdata_rejects_empty_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(status="ok", accounts=[])

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)

    with pytest.raises(ValueError, match=r"no accounts"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )


@pytest.mark.asyncio
async def test_import_account_tdata_preflight_blocks_existing_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If any tdata account_id is already in DB, the whole import aborts before touching disk."""
    await add_account(AccountCreate(account_id="111", label="pre-existing"))
    final_dir = settings.telegram.session_dir
    final_dir.mkdir(parents=True, exist_ok=True)
    existing_session = final_dir / "111.session"
    existing_session.write_bytes(b"original")

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"new-from-tdata")
    (staging / "222.session").write_bytes(b"new-from-tdata-2")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(user_id=111, session_path=str(staging / "111.session")),
                TdataAccountSummary(user_id=222, session_path=str(staging / "222.session")),
            ],
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)

    with pytest.raises(SessionAlreadyExistsError, match=r"111"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )

    # Pre-existing file is untouched, second tdata account did not land either.
    assert existing_session.read_bytes() == b"original"
    assert not (final_dir / "222.session").exists()


def _tdata_zip_payload() -> bytes:
    """A minimal tdata-shaped zip so ``_find_tdata_dir`` locates a tdata folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("tdata/key_datas", b"x")
    return buf.getvalue()


def _fake_tdesktop_writing_sessions(*user_ids: int) -> object:
    """Build a fake opentele2 ``TDesktop`` whose ``ToTelethon`` writes a .session.

    Mirrors production: opentele2 writes the Telethon session file to the path
    it is handed. Used to exercise the REAL ``convert_tdata_zip`` staging path
    (no fake convert), so tests can prove staged files land in — and never
    clobber — the live sessions dir.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415 - test-local

    accounts = []
    for uid in user_ids:

        async def _to_telethon(*, session: str, flag: object, _uid: int = uid) -> object:  # noqa: ARG001
            from pathlib import Path  # noqa: PLC0415 - test-local, Path is TYPE_CHECKING-only here

            Path(session).write_bytes(f"session-for-{_uid}".encode())
            client = MagicMock()

            async def _disconnect() -> None:
                return None

            client.disconnect = _disconnect
            return client

        acc = MagicMock()
        acc.UserId = uid
        acc.ToTelethon = _to_telethon
        accounts.append(acc)
    return MagicMock(accountsCount=len(accounts), accounts=accounts)


@pytest.mark.asyncio
async def test_import_account_tdata_lands_all_files_and_leaves_no_staging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean multi-account import lands every .session in the live dir; no leftovers.

    Drives the real ``convert_tdata_zip`` (opentele2 mocked) so the staging →
    preflight → move flow runs end-to-end.
    """
    fake_td = _fake_tdesktop_writing_sessions(111, 222)

    async def fake_check(request: object) -> TelegramSessionCheckResult:
        account_id = getattr(request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
        )

    monkeypatch.setattr("core.tdata_import.TDesktop", lambda **_kw: fake_td)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)

    payload = _tdata_zip_payload()
    result = await import_account_tdata(
        TdataConvertRequest(filename="tdata.zip", content=payload, label="Pool"),
    )

    assert {a.account_id for a in result.accounts} == {"111", "222"}
    final_dir = settings.telegram.session_dir
    assert (final_dir / "111.session").read_bytes() == b"session-for-111"
    assert (final_dir / "222.session").read_bytes() == b"session-for-222"
    # No tdata_staging_* dir left beside the sessions dir.
    leftovers = [p for p in final_dir.parent.iterdir() if p.name.startswith("tdata_staging_")]
    assert leftovers == [], f"staging dir must be cleaned up, found {leftovers}"


@pytest.mark.asyncio
async def test_import_account_tdata_reimport_does_not_clobber_live_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-importing a user_id already registered must leave the live .session intact.

    The audit bug: convert wrote directly into the live dir, overwriting the
    existing credential BEFORE preflight, then preflight raised — losing the
    original. With staging conversion, preflight blocks and the original stays.
    """
    await add_account(AccountCreate(account_id="111", label="existing"))
    final_dir = settings.telegram.session_dir
    final_dir.mkdir(parents=True, exist_ok=True)
    live_session = final_dir / "111.session"
    live_session.write_bytes(b"ORIGINAL-CREDENTIAL")

    fake_td = _fake_tdesktop_writing_sessions(111)
    monkeypatch.setattr("core.tdata_import.TDesktop", lambda **_kw: fake_td)

    with pytest.raises(SessionAlreadyExistsError, match=r"111"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=_tdata_zip_payload()),
        )

    # The live credential is byte-for-byte untouched.
    assert live_session.read_bytes() == b"ORIGINAL-CREDENTIAL"
    # And no staging dir leaked.
    leftovers = [p for p in final_dir.parent.iterdir() if p.name.startswith("tdata_staging_")]
    assert leftovers == []


@pytest.mark.asyncio
async def test_check_account_session_updates_status(monkeypatch: pytest.MonkeyPatch) -> None:
    await add_account(AccountCreate(account_id="account-2"))

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        return TelegramSessionCheckResult(
            account_id="account-2",
            session_path="sessions/account-2",
            status="alive",
            is_temporary=False,
            user_id=123,
            username="checked",
        )

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)

    account = await check_account_session(AccountCheckRequest(account_id="account-2"))
    page = await list_accounts_page(status="alive")

    assert account.status == "alive"
    assert (await account_stats()).active == 1
    row = page.items[0]
    assert row.username == "checked"
    assert health_for_status(row.status) == "ok"


@pytest.mark.asyncio
async def test_check_account_session_rejects_unknown_account() -> None:
    """A missing account_id must surface as a domain ValueError, not StopIteration."""
    with pytest.raises(ValueError, match=r"Unknown account: account-missing"):
        await check_account_session(AccountCheckRequest(account_id="account-missing"))
