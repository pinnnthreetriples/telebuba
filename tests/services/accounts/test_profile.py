"""Account profile mutation service tests."""

from __future__ import annotations

import pytest

from core.db import fetch_account
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate, AccountProfileUpdateRequest
from schemas.telegram_actions import ActionResult, GetUserProfile, UpdateProfile
from schemas.telegram_profile_snapshot import TelegramProfileSnapshot
from services.accounts import AccountActionError, add_account, update_account_profile


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
async def test_update_account_profile_invalidates_cache_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused profile edit still drops the cached snapshot.

    A partial commit (name applied, username refused — see the gateway's
    username-first ordering) leaves server state changed even when the action
    reports failure; serving the cached snapshot would show pre-edit fields.
    """
    invalidated: list[str] = []
    await add_account(AccountCreate(account_id="account-profile-inv"))

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="update_profile",
            account_id=account_id,
            error_type="UsernameOccupiedError",
            error_message="USERNAME_OCCUPIED",
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.profile.invalidate_account_profile_cache",
        invalidated.append,
    )

    with pytest.raises(AccountActionError):
        await update_account_profile(
            AccountProfileUpdateRequest(
                account_id="account-profile-inv",
                first_name="Alice",
                username="taken",
            ),
        )

    assert invalidated == ["account-profile-inv"]


@pytest.mark.asyncio
async def test_update_account_profile_invalidates_cache_when_db_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram write ok + DB snapshot write failing must still invalidate.

    The live server state HAS changed; a stale cached snapshot would hide it
    until the TTL lapses even though the edit succeeded on Telegram.
    """
    invalidated: list[str] = []
    await add_account(AccountCreate(account_id="account-profile-db"))

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    async def failing_snapshot_update(_data: object) -> object:
        msg = "db is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    monkeypatch.setattr(
        "services.accounts.profile.update_account_profile_snapshot",
        failing_snapshot_update,
    )
    monkeypatch.setattr(
        "services.accounts.profile.invalidate_account_profile_cache",
        invalidated.append,
    )

    with pytest.raises(RuntimeError):
        await update_account_profile(
            AccountProfileUpdateRequest(account_id="account-profile-db", first_name="Alice"),
        )

    assert invalidated == ["account-profile-db"]


def _patch_failed_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="update_profile",
            account_id=account_id,
            error_message="boom",
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)


async def _seed_profile(account_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create the account and store a known pre-edit snapshot in the DB."""
    await add_account(AccountCreate(account_id=account_id))

    async def ok_execute(acc_id: str, _action: object) -> ActionResult:
        return ActionResult(status="ok", action_type="update_profile", account_id=acc_id)

    monkeypatch.setattr("services.accounts.profile.execute", ok_execute)
    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id=account_id,
            first_name="Alice",
            last_name="L",
            username="oldname",
            bio="Bio",
        ),
    )


@pytest.mark.asyncio
async def test_partial_username_apply_resyncs_db_from_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Username applied + follow-up UpdateProfileRequest refused → DB gets confirmed state.

    The gateway sends the username FIRST, so a refused edit can still have
    changed the live username; the DB row must not keep the old one.
    """
    await _seed_profile("account-profile-partial", monkeypatch)
    read_actions: list[object] = []

    async def fake_read(_account_id: str, action: object) -> TelegramProfileSnapshot:
        read_actions.append(action)
        return TelegramProfileSnapshot(first_name="Alice", username="newname")

    _patch_failed_execute(monkeypatch)
    monkeypatch.setattr("services.accounts.profile.execute_read", fake_read)

    with pytest.raises(AccountActionError):
        await update_account_profile(
            AccountProfileUpdateRequest(
                account_id="account-profile-partial",
                first_name="Alice",
                username="newname",
            ),
        )

    assert len(read_actions) == 1
    assert isinstance(read_actions[0], GetUserProfile)
    account = await fetch_account("account-profile-partial")
    assert account is not None
    # Confirmed live state wins: new username, unset optionals cleared.
    assert account.username == "newname"
    assert account.last_name in (None, "")
    assert account.bio in (None, "")


@pytest.mark.asyncio
async def test_failed_edit_without_username_skips_resync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No username in the payload → no partial-apply window → no extra read."""
    await _seed_profile("account-profile-noresync", monkeypatch)
    read_actions: list[object] = []

    async def fake_read(_account_id: str, action: object) -> TelegramProfileSnapshot:
        read_actions.append(action)
        return TelegramProfileSnapshot(first_name="Alice")

    _patch_failed_execute(monkeypatch)
    monkeypatch.setattr("services.accounts.profile.execute_read", fake_read)

    with pytest.raises(AccountActionError):
        await update_account_profile(
            AccountProfileUpdateRequest(account_id="account-profile-noresync", first_name="Bob"),
        )

    assert read_actions == []


@pytest.mark.asyncio
async def test_refused_resync_read_still_surfaces_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood-blocked confirmation read is skipped silently; DB stays as-is."""
    await _seed_profile("account-profile-readfail", monkeypatch)

    async def failing_read(_account_id: str, _action: object) -> TelegramProfileSnapshot:
        reason = "FloodWaitError: wait of 30s"
        raise TelegramReadError(reason)

    _patch_failed_execute(monkeypatch)
    monkeypatch.setattr("services.accounts.profile.execute_read", failing_read)

    with pytest.raises(AccountActionError, match="boom"):
        await update_account_profile(
            AccountProfileUpdateRequest(
                account_id="account-profile-readfail",
                first_name="Alice",
                username="newname",
            ),
        )

    account = await fetch_account("account-profile-readfail")
    assert account is not None
    assert account.username == "oldname"
