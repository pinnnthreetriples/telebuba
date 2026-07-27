"""Account profile mutation service tests."""

from __future__ import annotations

import pytest

from core.db import fetch_account
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate, AccountProfileUpdateRequest
from schemas.telegram_actions import ActionResult, GetUserProfile, UpdateProfile
from schemas.telegram_profile_snapshot import TelegramProfileSnapshot
from services.accounts import AccountActionError, add_account, update_account_profile


def _patch_read(
    monkeypatch: pytest.MonkeyPatch,
    snapshot: TelegramProfileSnapshot | None = None,
) -> list[object]:
    """Stub the confirmation read and record its actions.

    ``snapshot=None`` makes the read fail, which is the pre-P3 behaviour of the
    happy path: the service falls back to persisting the request. Autouse-free on
    purpose — every test states what Telegram confirms.
    """
    calls: list[object] = []

    async def fake_read(_account_id: str, action: object) -> TelegramProfileSnapshot:
        calls.append(action)
        if snapshot is None:
            reason = "FloodWaitError: wait of 30s"
            raise TelegramReadError(reason)
        return snapshot

    monkeypatch.setattr("services.accounts.profile.execute_read", fake_read)
    return calls


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
    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="alice", bio="Bio"),
    )

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
    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="alice", bio="Bio"),
    )
    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-clear",
            first_name="Alice",
            last_name="L",
            username="alice",
            bio="Bio",
        ),
    )

    # Telegram now confirms every optional as unset.
    _patch_read(monkeypatch, TelegramProfileSnapshot(first_name="Alice"))
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
            error_type="RuntimeError",
            error_message="boom",
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    _patch_read(monkeypatch)

    # An unmapped exception's prose is not a code: the bounded status surfaces.
    with pytest.raises(ValueError, match="failed"):
        await update_account_profile(
            AccountProfileUpdateRequest(account_id="account-profile-fail", first_name="Alice"),
        )


@pytest.mark.asyncio
async def test_update_account_profile_none_fields_leave_db_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Contract: ``None`` means "leave unchanged" — in the action and on Telegram.

    The SPA sends ``""`` to clear; a ``None`` payload must neither clear the
    stored snapshot nor claim it did. The confirmation read reports the unchanged
    live values, so the row keeps them.
    """
    captured: list[object] = []
    await add_account(AccountCreate(account_id="account-profile-none"))

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="alice", bio="Bio"),
    )
    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-none",
            first_name="Alice",
            last_name="L",
            username="alice",
            bio="Bio",
        ),
    )

    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alicia", last_name="L", username="alice", bio="Bio"),
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
    _patch_read(monkeypatch)

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
            # The gateway maps the Telethon refusal to its stable code before
            # the result is built (``_profile._PROFILE_ERROR_CODES``).
            error_type="ProfileGatewayError",
            error_message="username_occupied",
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
    _patch_read(monkeypatch)
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
    """A generic (unmapped) refusal — its prose collapses to the ``failed`` code."""

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="update_profile",
            account_id=account_id,
            error_type="RuntimeError",
            error_message="boom",
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)


def _patch_refused_execute(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """A gateway refusal carrying one of the stable profile codes."""

    async def fake_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type="update_profile",
            account_id=account_id,
            error_type="ProfileGatewayError",
            error_message=code,
        )

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)


async def _seed_profile(account_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create the account and store a known pre-edit snapshot in the DB."""
    await add_account(AccountCreate(account_id=account_id))

    async def ok_execute(acc_id: str, _action: object) -> ActionResult:
        return ActionResult(status="ok", action_type="update_profile", account_id=acc_id)

    monkeypatch.setattr("services.accounts.profile.execute", ok_execute)
    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="oldname", bio="Bio"),
    )
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
async def test_unchanged_username_is_not_sent_to_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SPA re-submits the current handle on every save; that must not be an RPC.

    The gateway fires ``UpdateUsernameRequest`` whenever ``action.username`` is
    not ``None``, and it is the flood-sensitive call of the pair — a FloodWait from
    it writes the sticky ``flood_wait`` status, which blocks ``start_warming``. A
    bio-only edit therefore must leave the username out of the action entirely.
    """
    await _seed_profile("account-profile-samename", monkeypatch)
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="oldname", bio="New"),
    )

    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-samename",
            first_name="Alice",
            last_name="L",
            # Exactly what the DB row already holds.
            username="oldname",
            bio="New",
        ),
    )

    action = captured[-1]
    assert isinstance(action, UpdateProfile)
    assert action.username is None, "an unchanged handle must not reach updateUsername"
    assert action.bio == "New"


@pytest.mark.asyncio
async def test_changed_username_is_still_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The skip is an equality check, not a blanket suppression."""
    await _seed_profile("account-profile-newname", monkeypatch)
    captured: list[object] = []

    async def fake_execute(account_id: str, action: object) -> ActionResult:
        captured.append(action)
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", fake_execute)
    _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="newname", bio="Bio"),
    )

    await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-newname",
            first_name="Alice",
            username="newname",
        ),
    )

    action = captured[-1]
    assert isinstance(action, UpdateProfile)
    assert action.username == "newname"


@pytest.mark.asyncio
async def test_success_persists_the_bio_telegram_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram can accept ``updateProfile`` and silently ignore ``about``.

    ``accounts.bio`` had exactly one writer — the operator's request — so a
    dropped bio was stored as truth and then served from the row forever whenever
    a later live pull failed. The success path now persists what the confirmation
    read reports instead.
    """
    await _seed_profile("account-profile-drop", monkeypatch)

    async def ok_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", ok_execute)
    # Telegram kept the old bio: the young-account ``about`` drop.
    reads = _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", last_name="L", username="oldname", bio="Bio"),
    )

    account = await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-drop",
            first_name="Alice",
            last_name="L",
            username="oldname",
            bio="Fresh bio",
        ),
    )

    assert len(reads) == 1
    assert isinstance(reads[0], GetUserProfile)
    assert account.bio == "Bio", "the confirmed bio wins over the requested one"
    stored = await fetch_account("account-profile-drop")
    assert stored is not None
    assert stored.bio == "Bio"


@pytest.mark.asyncio
async def test_success_falls_back_to_the_request_when_the_read_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused confirmation read must not lose the operator's edit."""
    await _seed_profile("account-profile-readgone", monkeypatch)

    async def ok_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(status="ok", action_type="update_profile", account_id=account_id)

    monkeypatch.setattr("services.accounts.profile.execute", ok_execute)
    _patch_read(monkeypatch)

    account = await update_account_profile(
        AccountProfileUpdateRequest(
            account_id="account-profile-readgone",
            first_name="Alice",
            bio="Fresh bio",
        ),
    )

    assert account.bio == "Fresh bio"


@pytest.mark.asyncio
async def test_partial_username_apply_resyncs_db_from_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Username applied + follow-up UpdateProfileRequest refused → DB gets confirmed state.

    The gateway sends the username FIRST, so a refused edit can still have
    changed the live username; the DB row must not keep the old one.
    """
    await _seed_profile("account-profile-partial", monkeypatch)

    _patch_failed_execute(monkeypatch)
    read_actions = _patch_read(
        monkeypatch,
        TelegramProfileSnapshot(first_name="Alice", username="newname"),
    )

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


@pytest.mark.parametrize(
    "code",
    ["username_occupied", "username_invalid", "session_dead", "account_deactivated"],
)
@pytest.mark.asyncio
async def test_stable_refusals_skip_the_confirmation_read(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    """These refusals leave nothing to reconcile, so the read is pure waste.

    The two username codes come from the FIRST RPC of the dispatch — before
    anything changed — and a dead or deactivated session cannot serve a read at
    all. It used to fire for every failed save that carried a username, which is
    every save the SPA makes.
    """
    account_id = f"account-profile-{code.replace('_', '-')}"
    await _seed_profile(account_id, monkeypatch)

    _patch_refused_execute(monkeypatch, code)
    read_actions = _patch_read(monkeypatch, TelegramProfileSnapshot(first_name="Alice"))

    with pytest.raises(AccountActionError, match=code):
        await update_account_profile(
            AccountProfileUpdateRequest(
                account_id=account_id,
                first_name="Alice",
                username="newname",
            ),
        )

    assert read_actions == []


@pytest.mark.asyncio
async def test_unavailable_result_skips_the_confirmation_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool already failed to connect; a second connect is guaranteed to fail too."""
    await _seed_profile("account-profile-unavail", monkeypatch)

    async def unavailable_execute(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="unavailable",
            action_type="update_profile",
            account_id=account_id,
        )

    monkeypatch.setattr("services.accounts.profile.execute", unavailable_execute)
    read_actions = _patch_read(monkeypatch, TelegramProfileSnapshot(first_name="Alice"))

    with pytest.raises(AccountActionError, match="unavailable"):
        await update_account_profile(
            AccountProfileUpdateRequest(
                account_id="account-profile-unavail",
                first_name="Alice",
                username="newname",
            ),
        )

    assert read_actions == []


@pytest.mark.asyncio
async def test_refused_resync_read_still_surfaces_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A flood-blocked confirmation read is skipped silently; DB stays as-is."""
    await _seed_profile("account-profile-readfail", monkeypatch)

    _patch_failed_execute(monkeypatch)
    _patch_read(monkeypatch)

    with pytest.raises(AccountActionError, match=r"^failed$"):
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


@pytest.mark.asyncio
async def test_resync_with_unstorable_live_username_keeps_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live value our schema refuses must not replace the action's error.

    Fragment/NFT usernames can be 4 characters — shorter than the schema's
    5-char floor. The re-sync's ValidationError (a ValueError subclass) must be
    swallowed so the API still answers with the original stable code, not a
    pydantic dump.
    """
    await _seed_profile("account-profile-nft", monkeypatch)

    _patch_failed_execute(monkeypatch)
    _patch_read(monkeypatch, TelegramProfileSnapshot(first_name="Alice", username="nft1"))

    with pytest.raises(AccountActionError, match=r"^failed$"):
        await update_account_profile(
            AccountProfileUpdateRequest(
                account_id="account-profile-nft",
                first_name="Alice",
                username="newname",
            ),
        )

    account = await fetch_account("account-profile-nft")
    assert account is not None
    # Sync skipped wholesale: the DB keeps the pre-edit snapshot.
    assert account.username == "oldname"
