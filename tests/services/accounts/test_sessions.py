"""Account session and tdata import service tests."""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
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
    list_accounts,
    list_accounts_page,
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

    result = await import_account_tdata(
        TdataConvertRequest(filename="tdata.zip", content=b"x", label="My pool"),
    )

    assert [a.account_id for a in result.accounts] == ["111", "222"]
    assert all(a.status == "alive" for a in result.accounts)


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
async def test_import_account_tdata_rolls_back_on_mid_batch_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mid-batch failure must leave the DB and disk in their pre-import state."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")
    (staging / "222.session").write_bytes(b"sess-222")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(user_id=111, session_path=str(staging / "111.session")),
                TdataAccountSummary(user_id=222, session_path=str(staging / "222.session")),
            ],
        )

    call_count = {"n": 0}

    async def flaky_check(request: object) -> TelegramSessionCheckResult:
        call_count["n"] += 1
        if call_count["n"] == 2:
            msg = "boom on second account"
            raise RuntimeError(msg)
        account_id = getattr(request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
            user_id=int(account_id),
            username=f"u{account_id}",
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", flaky_check)

    with pytest.raises(RuntimeError, match=r"boom"):
        await import_account_tdata(
            TdataConvertRequest(filename="tdata.zip", content=b"x"),
        )

    persisted = await list_accounts()
    assert persisted.accounts == []
    final_dir = settings.telegram.session_dir
    assert not (final_dir / "111.session").exists()
    assert not (final_dir / "222.session").exists()


def _stage_failing_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two staged tdata accounts where the second account's check explodes.

    Same shape as ``test_import_account_tdata_rolls_back_on_mid_batch_failure``,
    reused by the two rollback-mechanics tests below.
    """
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "111.session").write_bytes(b"sess-111")
    (staging / "222.session").write_bytes(b"sess-222")

    async def fake_convert(_req: TdataConvertRequest, _dir: object) -> TdataConvertResult:
        return TdataConvertResult(
            status="ok",
            accounts=[
                TdataAccountSummary(user_id=111, session_path=str(staging / "111.session")),
                TdataAccountSummary(user_id=222, session_path=str(staging / "222.session")),
            ],
        )

    calls = {"n": 0}

    async def flaky_check(request: object) -> TelegramSessionCheckResult:
        calls["n"] += 1
        if calls["n"] == 2:
            msg = "boom on second account"
            raise RuntimeError(msg)
        account_id = getattr(request, "account_id", "?")
        return TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status="alive",
            is_temporary=False,
        )

    monkeypatch.setattr("services.accounts.sessions.convert_tdata_zip", fake_convert)
    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", flaky_check)


@pytest.mark.asyncio
async def test_tdata_rollback_evicts_the_pooled_client_per_placed_account(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The rollback runs under the pool tombstone, like ``lifecycle.remove_account``.

    ``check_account_session`` has already pooled a client for every placed
    account, and on Windows that live ``.session`` handle is what makes the
    rollback's unlink raise — leaving an orphan file with no DB row, which
    preflight then refuses to re-import forever.
    """
    _stage_failing_import(monkeypatch, tmp_path)

    evicted: list[str] = []

    async def fake_evict(account_id: str) -> None:
        evicted.append(account_id)

    monkeypatch.setattr("core.telegram_client._pool.evict_client", fake_evict)

    with pytest.raises(RuntimeError, match=r"boom"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    assert evicted == ["111", "222"], "every placed session must be unlinked with no live handle"


@pytest.mark.asyncio
async def test_tdata_rollback_logs_an_unlink_it_could_not_perform(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed unlink is reported, never swallowed.

    The silent ``suppress(OSError)`` is what turned a retryable import failure
    into a permanent, operator-unfixable block: the orphaned ``.session`` has no
    account row to delete through the UI, and preflight refuses the retry with
    "delete it before importing". The file may survive — the log entry is what
    tells the operator which one to remove.
    """
    _stage_failing_import(monkeypatch, tmp_path)

    events: list[str] = []

    async def fake_log(
        level: str,  # noqa: ARG001
        event: str,
        account_id: str | None = None,  # noqa: ARG001
        extra: dict[str, object] | None = None,  # noqa: ARG001
    ) -> None:
        events.append(event)

    def refuse_unlink(_self: object, *, missing_ok: bool = False) -> None:  # noqa: ARG001
        # What Windows does when the pooled client still holds the handle.
        msg = "used by another process"
        raise OSError(msg)

    monkeypatch.setattr("services.accounts._tdata.log_event", fake_log)
    monkeypatch.setattr("pathlib.Path.unlink", refuse_unlink)

    with pytest.raises(RuntimeError, match=r"boom"):
        await import_account_tdata(TdataConvertRequest(filename="tdata.zip", content=b"x"))

    assert "tdata_rollback_unlink_failed" in events
    assert "tdata_import_rolled_back" in events


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
