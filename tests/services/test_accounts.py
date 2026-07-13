"""Tests for the accounts service layer."""

from __future__ import annotations

import io
import zipfile
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    update_account_from_session_check,
    update_proxy_check,
    upsert_spam_status,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountProfileUpdateRequest,
    AccountSessionFileImport,
    AccountStatus,
    health_for_status,
)
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoRemove,
    AccountProfilePhotoSetMain,
    AccountProfilePhotoUpload,
    AccountStoryPin,
    AccountStoryRemove,
    AccountStoryUpload,
)
from schemas.proxy import ProxyCheckUpdate
from schemas.spam_status import SpamStatusVerdict
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    PostStory,
    RemoveProfilePhoto,
    RemoveStory,
    SetMainProfilePhoto,
    SetProfilePhoto,
    ToggleStoryPinned,
    UpdateProfile,
)
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import (
    AccountActionError,
    SessionAlreadyExistsError,
    account_stats,
    add_account,
    add_account_profile_music,
    check_account_session,
    evaluate_account_geo,
    import_account_session,
    import_account_tdata,
    list_accounts,
    list_accounts_page,
    list_listener_accounts,
    post_account_story,
    remove_account_profile_photo,
    remove_account_story,
    set_account_main_profile_photo,
    set_account_profile_photo,
    set_account_story_pinned,
    update_account_profile,
)
from tests.factories import seed_account_proxy

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.mark.asyncio
async def test_add_account_creates_fingerprint_and_page_row() -> None:
    account = await add_account(
        AccountCreate(account_id="account-1", label="Main", session_name="session-1"),
    )
    page = await list_accounts_page()
    stats = await account_stats()

    assert account.account_id == "account-1"
    assert stats.total == 1
    assert stats.needs_code == 1  # a never-checked account still needs a login code
    row = page.items[0]
    assert row.label == "Main"
    assert row.device_model is not None
    # A freshly added account has not been checked yet -> warn (amber).
    assert health_for_status(row.status) == "warn"


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
async def test_import_account_session_rejects_non_session_file() -> None:
    with pytest.raises(ValueError, match=r"\.session"):
        await import_account_session(
            AccountSessionFileImport(filename="not-session.txt", content=b"content"),
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
async def test_list_listener_accounts_keeps_only_live_sessions() -> None:
    """Only accounts with an authorized session may act as the neurocomment listener."""
    await add_account(AccountCreate(account_id="never", label="Never checked"))
    await add_account(AccountCreate(account_id="live", label="Live"))
    await add_account(AccountCreate(account_id="dead", label="No session"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="live",
            session_path="sessions/live",
            status="alive",
            is_temporary=False,
        ),
    )
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="dead",
            session_path="sessions/dead",
            status="session_error",
            is_temporary=False,
            error_type="AuthKeyError",
            error_message="session revoked",
        ),
    )

    result = await list_listener_accounts()

    # "never" (status new → warn) and "dead" (session_error → fail) are excluded.
    assert [a.account_id for a in result.accounts] == ["live"]


@pytest.mark.asyncio
async def test_list_accounts_page_filters_query_and_status() -> None:
    await add_account(AccountCreate(account_id="one", label="Alpha"))
    await add_account(AccountCreate(account_id="two", label="Beta"))

    query_page = await list_accounts_page(query="alp")
    status_page = await list_accounts_page(status="alive")

    assert [row.account_id for row in query_page.items] == ["one"]
    assert status_page.items == []
    # Stats stay fleet-wide regardless of the filtered page.
    assert (await account_stats()).total == 2


@pytest.mark.asyncio
async def test_list_accounts_page_paginates_by_cursor() -> None:
    """limit/cursor must reach the DB — the UI never loads everything to slice in Python."""
    for ident in ("a", "b", "c", "d", "e"):
        await add_account(AccountCreate(account_id=ident, label=f"Label {ident}"))

    page = await list_accounts_page(limit=2)
    assert len(page.items) == 2
    assert page.next_cursor is not None

    next_page = await list_accounts_page(limit=2, cursor=page.next_cursor)
    assert len(next_page.items) == 2
    assert {row.account_id for row in page.items} & {
        row.account_id for row in next_page.items
    } == set()


async def _set_status(account_id: str, status: AccountStatus) -> None:
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path=f"sessions/{account_id}",
            status=status,  # ty: ignore[invalid-argument-type] — SessionCheckStatus ⊇ used set
            is_temporary=False,
        ),
    )


@pytest.mark.asyncio
async def test_account_stats_counts_whole_fleet_across_pages() -> None:
    """Stats span the entire table (one grouped query), not a single 20-row page.

    Seeds >1 page of accounts across every design bucket and asserts the tile
    counts are the fleet-wide totals, independent of pagination.
    """
    # 10 alive, 6 flood_wait (idle/spam), 5 unauthorized + 4 new (needs_code),
    # 3 session_error + 2 account_error (problem) = 30 accounts (> one 20-row page).
    plan: list[tuple[str, AccountStatus, int]] = [
        ("alive", "alive", 10),
        ("flood", "flood_wait", 6),
        ("unauth", "unauthorized", 5),
        ("new", "new", 4),
        ("serr", "session_error", 3),
        ("aerr", "account_error", 2),
    ]
    for prefix, status, count in plan:
        for i in range(count):
            ident = f"{prefix}-{i}"
            await add_account(AccountCreate(account_id=ident, label=ident))
            if status != "new":  # "new" is the create default; no flip needed.
                await _set_status(ident, status)

    stats = await account_stats()

    assert stats.total == 30
    assert stats.active == 10  # alive
    assert stats.idle == 6  # flood_wait (spam-limited)
    assert stats.needs_code == 9  # 5 unauthorized + 4 new
    assert stats.problem == 5  # 3 session_error + 2 account_error

    # Independence from pagination: a single page never sees the whole fleet.
    first_page = await list_accounts_page(limit=20)
    assert len(first_page.items) == 20
    assert first_page.next_cursor is not None
    assert stats.total > len(first_page.items)


@pytest.mark.asyncio
async def test_list_accounts_page_enriches_trust_and_spam() -> None:
    """The accounts page carries a computed Trust Score + last cached spam verdict."""
    await add_account(AccountCreate(account_id="limited"))
    await add_account(AccountCreate(account_id="unprobed"))
    await upsert_spam_status(
        SpamStatusVerdict(
            account_id="limited",
            status="limited",
            detail="restricted until 2026",
            checked_at="2026-06-30T00:00:00+00:00",
        ),
    )

    page = await list_accounts_page()
    rows = {row.account_id: row for row in page.items}

    # Trust is computed for every row regardless of whether a spam probe ran.
    assert rows["limited"].trust_score is not None
    assert rows["limited"].trust_band is not None
    assert rows["unprobed"].trust_score is not None

    # The cached spam verdict surfaces on the probed row and docks its score.
    assert rows["limited"].spam_status == "limited"
    assert rows["limited"].spam_detail == "restricted until 2026"
    assert rows["unprobed"].spam_status is None
    assert rows["limited"].trust_score < rows["unprobed"].trust_score

    # The device fingerprint's system language is surfaced for the edit card.
    assert rows["limited"].device_lang is not None


@pytest.mark.asyncio
async def test_list_accounts_page_search_uses_db_filter() -> None:
    await add_account(AccountCreate(account_id="alpha", label="Alpha"))
    await add_account(AccountCreate(account_id="beta", label="Beta"))
    await add_account(AccountCreate(account_id="alphabet", label="Alphabet"))

    page = await list_accounts_page(query="alpha")
    ids = {row.account_id for row in page.items}
    assert ids == {"alpha", "alphabet"}
    # Stats stay the whole table — search narrows the page, not the totals.
    assert (await account_stats()).total == 3


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


@pytest.mark.asyncio
async def test_update_account_profile_executes_action_and_persists_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    await add_account(AccountCreate(account_id="account-profile"))

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)

    account = await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile",
            first_name="Alice",
            last_name="L",
            username="alice",
            bio="Bio",
        ),
    )

    assert account.first_name == "Alice"
    assert account.last_name == "L"
    assert account.username == "alice"
    assert account.bio == "Bio"
    assert captured


@pytest.mark.asyncio
async def test_update_account_profile_can_clear_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    await add_account(AccountCreate(account_id="account-profile-clear"))

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-clear",
            first_name="Alice",
            last_name="L",
            username="alice",
            bio="Bio",
        ),
    )

    account = await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-clear",
            first_name="Alice",
            last_name="",
            username="",
            bio="",
        ),
    )

    assert account.last_name == ""
    assert account.username == ""
    assert account.bio == ""
    assert isinstance(captured[-1], UpdateProfile)
    assert captured[-1].username == ""


@pytest.mark.asyncio
async def test_update_account_profile_surfaces_action_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="account-profile-fail"))

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="update_profile",
            account_id=account_id,
            error_message="boom",
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)

    with pytest.raises(ValueError, match="boom"):
        await update_account_profile(
            AccountProfileUpdateRequest(account_id="account-profile-fail", first_name="Alice"),
        )


@pytest.mark.asyncio
async def test_update_account_profile_none_fields_leave_db_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract: ``None`` means "leave unchanged" — in the DB row and the action.

    The SPA sends ``""`` to clear; a ``None`` payload must neither clear the
    stored snapshot nor claim it did.
    """
    captured: list[object] = []
    await add_account(AccountCreate(account_id="account-profile-none"))

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-none",
            first_name="Alice",
            last_name="L",
            username="alice",
            bio="Bio",
        ),
    )

    account = await update_account_profile(
        AccountProfileUpdateRequest(account_id="account-profile-none", first_name="Alicia"),
    )

    assert account.first_name == "Alicia"
    assert account.last_name == "L"
    assert account.username == "alice"
    assert account.bio == "Bio"
    action = captured[-1]
    assert isinstance(action, UpdateProfile)
    assert action.last_name is None
    assert action.username is None
    assert action.bio is None


@pytest.mark.asyncio
async def test_update_account_profile_flood_wait_carries_retry_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood-limited update raises ``AccountActionError`` with the wait duration."""
    await add_account(AccountCreate(account_id="account-profile-flood"))

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="flood_wait",
            action_type="update_profile",
            account_id=account_id,
            flood_wait_seconds=345,
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)

    with pytest.raises(AccountActionError, match="flood_wait") as excinfo:
        await update_account_profile(
            AccountProfileUpdateRequest(account_id="account-profile-flood", first_name="Alice"),
        )
    assert excinfo.value.code == "flood_wait"
    assert excinfo.value.retry_after_seconds == 345


@pytest.mark.asyncio
async def test_set_account_profile_photo_executes_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="set_profile_photo", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await set_account_profile_photo(
        AccountProfilePhotoUpload(
            account_id="account-photo",
            filename="avatar.jpg",
            content=b"jpg",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], SetProfilePhoto)


@pytest.mark.asyncio
async def test_media_upload_invalidates_profile_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three media services call ``invalidate_account_profile_cache``.

    Regression: after PR #96 the dialog cached the live snapshot, but only
    ``update_account_profile`` invalidated it on save. Media uploads fell
    through with ``force_refresh=True`` at the UI layer; the new
    optimistic-update flow removed that, so service-level invalidation is
    the only safety net left.
    """
    invalidated: list[str] = []

    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        return ActionResult(
            status="ok",
            action_type=getattr(action, "action_type", "unknown"),
            account_id=account_id,
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    await set_account_profile_photo(
        AccountProfilePhotoUpload(
            account_id="acc-photo",
            filename="a.jpg",
            content=b"jpg",
        ),
    )
    await post_account_story(
        AccountStoryUpload(
            account_id="acc-story",
            filename="s.jpg",
            content=b"jpg",
            media_kind="image",
        ),
    )
    await add_account_profile_music(
        AccountProfileMusicUpload(
            account_id="acc-music",
            filename="t.mp3",
            content=b"mp3",
        ),
    )

    assert invalidated == ["acc-photo", "acc-story", "acc-music"]


@pytest.mark.asyncio
async def test_post_account_story_executes_story_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="post_story", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await post_account_story(
        AccountStoryUpload(
            account_id="account-story",
            filename="story.mp4",
            content=b"mp4",
            media_kind="video",
            caption="Story",
            privacy_preset="public",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], PostStory)
    assert captured[0].privacy_preset == "public"


@pytest.mark.asyncio
async def test_post_account_story_collage_passes_extra_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A collage upload threads image #1 + extra_images + layout into ``PostStory``."""
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="post_story", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await post_account_story(
        AccountStoryUpload(
            account_id="acc-collage",
            filename="s.jpg",
            content=b"first",
            media_kind="image",
            extra_images=[b"second", b"third"],
            collage_layout="v3",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], PostStory)
    assert captured[0].extra_images == [b"second", b"third"]
    assert captured[0].collage_layout == "v3"


@pytest.mark.asyncio
async def test_post_account_story_collage_rejects_too_many_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_collage_max_images", 3)

    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(AccountActionError) as excinfo:
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.jpg",
                content=b"first",
                media_kind="image",
                extra_images=[b"a", b"b", b"c"],  # total 4 > cap 3
            ),
        )
    assert str(excinfo.value) == "story_collage_too_many_images"


@pytest.mark.asyncio
async def test_post_account_story_rejects_extra_images_on_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(AccountActionError) as excinfo:
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.mp4",
                content=b"vid",
                media_kind="video",
                extra_images=[b"x"],
            ),
        )
    assert str(excinfo.value) == "story_collage_requires_image"


@pytest.mark.asyncio
async def test_post_account_story_collage_rejects_oversize_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.profile_media, "story_image_max_bytes", 3)

    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(ValueError, match="too large"):
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.jpg",
                content=b"ok",  # 2 bytes, under the 3-byte cap
                media_kind="image",
                extra_images=[b"way-too-big"],
            ),
        )


@pytest.mark.asyncio
async def test_post_account_story_collage_rejects_bad_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        msg = "execute should not be reached"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(ValueError, match="must be one of"):
        await post_account_story(
            AccountStoryUpload(
                account_id="acc-collage",
                filename="s.txt",  # not an image suffix
                content=b"first",
                media_kind="image",
                extra_images=[b"second"],
            ),
        )


@pytest.mark.asyncio
async def test_add_account_profile_music_executes_music_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="add_profile_music", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    result = await add_account_profile_music(
        AccountProfileMusicUpload(
            account_id="account-music",
            filename="track.mp3",
            content=b"mp3",
            title="Track",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], AddProfileMusic)
    assert captured[0].title == "Track"


@pytest.mark.asyncio
async def test_remove_account_profile_photo_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remove-photo service must reach Telegram with the InputPhoto id triple.

    Mirrors the music-removal contract: passing only ``photo_id`` is not enough
    — ``access_hash`` and ``file_reference`` are required for Telethon's
    ``DeletePhotosRequest``, and the in-process snapshot cache has to be
    cleared so the next dialog open shows the new photo set.
    """
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="remove_profile_photo", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await remove_account_profile_photo(
        AccountProfilePhotoRemove(
            account_id="account-photo-remove",
            photo_id=4242,
            access_hash=7,
            file_reference=b"\x01\x02",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], RemoveProfilePhoto)
    assert captured[0].photo_id == 4242
    assert captured[0].access_hash == 7
    assert captured[0].file_reference == b"\x01\x02"
    assert invalidated == ["account-photo-remove"]


@pytest.mark.asyncio
async def test_set_account_main_profile_photo_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Сделать основным» must reach Telegram with the InputPhoto triple + clear cache."""
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(
            status="ok", action_type="set_main_profile_photo", account_id=account_id
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await set_account_main_profile_photo(
        AccountProfilePhotoSetMain(
            account_id="account-photo-main",
            photo_id=4242,
            access_hash=7,
            file_reference=b"\x01\x02",
        ),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], SetMainProfilePhoto)
    assert captured[0].photo_id == 4242
    assert captured[0].access_hash == 7
    assert captured[0].file_reference == b"\x01\x02"
    assert invalidated == ["account-photo-main"]


@pytest.mark.asyncio
async def test_set_account_main_profile_photo_invalidates_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FAILED «Сделать основным» must still drop the cached profile snapshot.

    Regression (debug.log 2026-07-13 18:11:39): a failed promote kept the stale
    snapshot alive, so the dialog kept offering photo ids that no longer existed
    on the server and the operator re-clicked dead entries. Invalidate whether
    the action succeeded or not — the next open re-reads live state.
    """
    invalidated: list[str] = []

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="set_main_profile_photo",
            account_id=account_id,
            error_type="RuntimeError",
            error_message="Target profile photo is no longer in the account's history",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    with pytest.raises(AccountActionError):
        await set_account_main_profile_photo(
            AccountProfilePhotoSetMain(
                account_id="account-photo-main-failed",
                photo_id=4242,
                access_hash=7,
                file_reference=b"\x01\x02",
            ),
        )

    assert invalidated == ["account-photo-main-failed"]


@pytest.mark.asyncio
async def test_remove_account_story_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service must reach Telegram with ``RemoveStory`` + clear the profile cache.

    Telegram's deleteStories doesn't error on unknown IDs (drops them
    silently), so the only signals we test are the action shape and the
    cache invalidation — both of which the optimistic UI relies on.
    """
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="remove_story", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await remove_account_story(
        AccountStoryRemove(account_id="account-story-remove", story_id=9876),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], RemoveStory)
    assert captured[0].story_id == 9876
    assert invalidated == ["account-story-remove"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [True, False])
async def test_set_account_story_pinned_executes_action_and_invalidates_cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pinned: bool,
) -> None:
    """Pinning/unpinning reaches Telegram with the target state + clears the cache."""
    captured: list[object] = []
    invalidated: list[str] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="toggle_story_pinned", account_id=account_id)

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.media.invalidate_account_profile_cache",
        invalidated.append,
    )

    result = await set_account_story_pinned(
        AccountStoryPin(account_id="account-story-pin", story_id=3210, pinned=pinned),
    )

    assert result.status == "ok"
    assert isinstance(captured[0], ToggleStoryPinned)
    assert captured[0].story_id == 3210
    assert captured[0].pinned is pinned
    assert invalidated == ["account-story-pin"]


@pytest.mark.asyncio
async def test_set_account_story_pinned_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Telegram refusal surfaces as ``AccountActionError`` (mapped to the envelope)."""

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="toggle_story_pinned",
            account_id=account_id,
            error_type="RPCError",
            error_message="STORY_ID_INVALID",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(AccountActionError):
        await set_account_story_pinned(
            AccountStoryPin(account_id="account-story-pin", story_id=1, pinned=True),
        )


@pytest.mark.asyncio
async def test_remove_account_profile_photo_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram refusals surface as ``ValueError`` — the UI shows the message inline."""

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="remove_profile_photo",
            account_id=account_id,
            error_type="RPCError",
            error_message="PHOTO_INVALID",
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)

    with pytest.raises(ValueError, match="PHOTO_INVALID"):
        await remove_account_profile_photo(
            AccountProfilePhotoRemove(
                account_id="acc",
                photo_id=1,
                access_hash=2,
                file_reference=b"\x03",
            ),
        )


@pytest.mark.asyncio
async def test_profile_media_rejects_wrong_extension() -> None:
    with pytest.raises(ValueError, match="profile photo must be one of"):
        await set_account_profile_photo(
            AccountProfilePhotoUpload(
                account_id="account-photo",
                filename="avatar.gif",
                content=b"gif",
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_health"),
    [
        ("alive", "ok"),
        ("new", "warn"),
        ("flood_wait", "warn"),
        ("network_error", "warn"),
        ("proxy_error", "warn"),
        ("unknown_error", "warn"),
        ("unauthorized", "fail"),
        ("session_error", "fail"),
        ("account_error", "fail"),
    ],
)
async def test_health_taxonomy_matches_status(
    monkeypatch: pytest.MonkeyPatch,
    status: AccountStatus,
    expected_health: str,
) -> None:
    """Every AccountStatus maps to exactly one of ok / warn / fail."""
    await add_account(AccountCreate(account_id="acc-h"))
    if status == "new":
        page = await list_accounts_page()
        assert health_for_status(page.items[0].status) == expected_health
        # Row carries the RAW status enum — the UI translates it to RU once.
        assert page.items[0].status == "new"
        return

    async def fake_check(_request: object) -> TelegramSessionCheckResult:
        return TelegramSessionCheckResult(
            account_id="acc-h",
            session_path="sessions/acc-h",
            status=status,
            is_temporary=status not in {"alive", "unauthorized", "session_error", "account_error"},
        )

    monkeypatch.setattr("services.accounts.sessions.check_telegram_session", fake_check)
    await check_account_session(AccountCheckRequest(account_id="acc-h"))
    page = await list_accounts_page()
    assert health_for_status(page.items[0].status) == expected_health
    # Row carries the RAW status enum (e.g. "network_error"), not an English
    # label — the UI is the single translation point. Guards the regression
    # where the service emitted "Network"/"Proxy"/"Unknown" and the UI RU map
    # (keyed "Network error"/…) silently failed to translate them.
    assert page.items[0].status == status


@pytest.mark.asyncio
async def test_evaluate_account_geo_flags_mismatch() -> None:
    await add_account(AccountCreate(account_id="acc-1"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="acc-1",
            session_path="acc-1",
            status="alive",
            is_temporary=False,
            phone="+77011234567",
        ),
    )
    proxy_id = await seed_account_proxy("acc-1", host="h")
    await update_proxy_check(
        ProxyCheckUpdate(proxy_id=proxy_id, status="tcp_working", country_code="US"),
    )

    verdict = await evaluate_account_geo("acc-1")

    assert verdict.status == "mismatch"
    assert verdict.phone_country == "KZ"
    assert verdict.proxy_country == "US"


@pytest.mark.asyncio
async def test_evaluate_account_geo_unknown_for_missing_account() -> None:
    verdict = await evaluate_account_geo("ghost")
    assert verdict.status == "unknown"
