"""Tests for the accounts service layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    configure_database,
    update_account_from_session_check,
    update_account_proxy_check,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import (
    AccountCheckRequest,
    AccountCreate,
    AccountFilter,
    AccountProfileUpdateRequest,
    AccountSessionFileImport,
    AccountStatus,
)
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoUpload,
    AccountStoryUpload,
)
from schemas.proxy import (
    AccountProxyCheckRequest,
    AccountProxyCheckUpdate,
    AccountProxyDelete,
    AccountProxyUpsert,
    ProxyCheckResult,
)
from schemas.tdata import TdataAccountSummary, TdataConvertRequest, TdataConvertResult
from schemas.telegram_actions import (
    ActionResult,
    AddProfileMusic,
    PostStory,
    SetProfilePhoto,
    UpdateProfile,
)
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import (
    SessionAlreadyExistsError,
    add_account,
    add_account_profile_music,
    check_account_proxy,
    check_account_session,
    delete_account_proxy,
    evaluate_account_geo,
    import_account_session,
    import_account_tdata,
    list_accounts,
    load_accounts_table,
    post_account_story,
    save_account_proxy,
    set_account_profile_photo,
    update_account_profile,
)
from services.accounts._table import _format_last_checked

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


@pytest.mark.parametrize(
    ("offset_seconds", "expected"),
    [
        (0, "0s ago"),
        (45, "45s ago"),
        (60, "1m ago"),
        (90, "1m ago"),
        (3_600, "1h ago"),
        (3_600 * 5, "5h ago"),
        (86_400, "1d ago"),
        (86_400 * 7, "7d ago"),
    ],
)
def test_format_last_checked_relative(offset_seconds: int, expected: str) -> None:
    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    moment = now - timedelta(seconds=offset_seconds)
    assert _format_last_checked(moment.isoformat(), now=now) == expected


def test_format_last_checked_never_for_empty() -> None:
    assert _format_last_checked(None) == "never"
    assert _format_last_checked("") == "never"


def test_format_last_checked_passes_through_garbage() -> None:
    assert _format_last_checked("not-a-date") == "not-a-date"


@pytest.mark.asyncio
async def test_add_account_creates_fingerprint_and_table_row() -> None:
    account = await add_account(
        AccountCreate(account_id="account-1", label="Main", session_name="session-1"),
    )
    state = await load_accounts_table(AccountFilter())

    assert account.account_id == "account-1"
    assert state.summary.total == 1
    assert state.summary.never_checked == 1
    assert state.rows[0].label == "Main"
    assert state.rows[0].device != "-"
    # A freshly added account has not been checked yet -> warn (amber).
    assert state.rows[0].health == "warn"


@pytest.mark.asyncio
async def test_import_account_session_saves_file_and_creates_account() -> None:
    account = await import_account_session(
        AccountSessionFileImport(
            filename="real-account.session",
            content=b"sqlite session bytes",
            label="Real account",
        ),
    )
    state = await load_accounts_table(AccountFilter())

    assert account.account_id == "real-account"
    assert account.session_name == "real-account"
    assert (
        settings.telegram.session_dir / "real-account.session"
    ).read_bytes() == b"sqlite session bytes"
    assert state.rows[0].label == "Real account"
    assert state.rows[0].session == "real-account"


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
async def test_load_accounts_table_filters_query_and_status() -> None:
    await add_account(AccountCreate(account_id="one", label="Alpha"))
    await add_account(AccountCreate(account_id="two", label="Beta"))

    query_state = await load_accounts_table(AccountFilter(query="alp"))
    status_state = await load_accounts_table(AccountFilter(status="alive"))

    assert [row.account_id for row in query_state.rows] == ["one"]
    assert status_state.rows == []
    assert status_state.summary.total == 2


@pytest.mark.asyncio
async def test_load_accounts_table_paginates_in_db() -> None:
    """limit/offset must reach the DB — UI no longer loads everything to slice in Python."""
    for ident in ("a", "b", "c", "d", "e"):
        await add_account(AccountCreate(account_id=ident, label=f"Label {ident}"))

    page = await load_accounts_table(AccountFilter(limit=2, offset=0))
    assert len(page.rows) == 2
    # Summary still reflects the whole table.
    assert page.summary.total == 5

    next_page = await load_accounts_table(AccountFilter(limit=2, offset=2))
    assert len(next_page.rows) == 2
    assert {row.account_id for row in page.rows} & {
        row.account_id for row in next_page.rows
    } == set()


@pytest.mark.asyncio
async def test_load_accounts_table_search_uses_db_filter() -> None:
    await add_account(AccountCreate(account_id="alpha", label="Alpha"))
    await add_account(AccountCreate(account_id="beta", label="Beta"))
    await add_account(AccountCreate(account_id="alphabet", label="Alphabet"))

    state = await load_accounts_table(AccountFilter(query="alpha"))
    ids = {row.account_id for row in state.rows}
    assert ids == {"alpha", "alphabet"}
    # Summary is still the whole table — search narrows rows, not totals.
    assert state.summary.total == 3


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
    state = await load_accounts_table(AccountFilter(status="alive"))

    assert account.status == "alive"
    assert state.summary.alive == 1
    assert state.rows[0].telegram == "@checked"
    assert state.rows[0].health == "ok"


@pytest.mark.asyncio
async def test_check_account_session_rejects_unknown_account() -> None:
    """A missing account_id must surface as a domain ValueError, not StopIteration."""
    with pytest.raises(ValueError, match=r"Unknown account: account-missing"):
        await check_account_session(AccountCheckRequest(account_id="account-missing"))


@pytest.mark.asyncio
async def test_save_and_delete_account_proxy_updates_table_row() -> None:
    await add_account(AccountCreate(account_id="account-proxy"))

    proxy = await save_account_proxy(
        AccountProxyUpsert(
            account_id="account-proxy",
            proxy_type="socks5",
            host="127.0.0.1",
            port=9050,
            username="alice",
            password="secret",  # noqa: S106 - test fixture value, not a real credential.
        ),
    )
    with_proxy = await load_accounts_table(AccountFilter())

    assert proxy.username == "a***e"
    assert with_proxy.rows[0].proxy == "SOCKS5 127.0.0.1:9050"

    await delete_account_proxy(AccountProxyDelete(account_id="account-proxy"))
    without_proxy = await load_accounts_table(AccountFilter())

    assert without_proxy.rows[0].proxy == "-"


@pytest.mark.asyncio
async def test_check_account_proxy_persists_route_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="account-proxy-check"))
    await save_account_proxy(
        AccountProxyUpsert(
            account_id="account-proxy-check",
            proxy_type="socks5",
            host="127.0.0.1",
            port=9050,
        ),
    )

    async def fake_check(_proxy: object) -> ProxyCheckResult:
        return ProxyCheckResult(
            status="tcp_working",
            exit_ip="45.130.253.155",
            country_code="NL",
            country_name="Netherlands",
        )

    monkeypatch.setattr("services.accounts.proxy.check_proxy_connectivity", fake_check)

    proxy = await check_account_proxy(AccountProxyCheckRequest(account_id="account-proxy-check"))
    state = await load_accounts_table(AccountFilter())

    assert proxy.status == "tcp_working"
    assert proxy.exit_ip == "45.130.253.155"
    assert state.rows[0].proxy_status == "tcp_working"
    assert state.rows[0].proxy_country_code == "NL"
    assert state.rows[0].proxy_country_name == "Netherlands"
    # Country code is surfaced in the main proxy label so it's visible at a glance
    # in the table cell, not only in the caption row underneath.
    assert "NL" in state.rows[0].proxy


@pytest.mark.asyncio
async def test_check_account_proxy_requires_saved_proxy() -> None:
    await add_account(AccountCreate(account_id="account-without-proxy"))

    with pytest.raises(ValueError, match="Proxy not found"):
        await check_account_proxy(AccountProxyCheckRequest(account_id="account-without-proxy"))


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
        state = await load_accounts_table(AccountFilter())
        assert state.rows[0].health == expected_health
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
    state = await load_accounts_table(AccountFilter())
    assert state.rows[0].health == expected_health


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
    await save_account_proxy(
        AccountProxyUpsert(account_id="acc-1", proxy_type="socks5", host="h", port=1080),
    )
    await update_account_proxy_check(
        AccountProxyCheckUpdate(account_id="acc-1", status="tcp_working", country_code="US"),
    )

    verdict = await evaluate_account_geo("acc-1")

    assert verdict.status == "mismatch"
    assert verdict.phone_country == "KZ"
    assert verdict.proxy_country == "US"


@pytest.mark.asyncio
async def test_evaluate_account_geo_unknown_for_missing_account() -> None:
    verdict = await evaluate_account_geo("ghost")
    assert verdict.status == "unknown"
