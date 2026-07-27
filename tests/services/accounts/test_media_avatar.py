"""Post-mutation avatar re-sync wiring for the media service.

The refresh itself (pool borrow + thumb download + DB write) is covered in
``tests.core.telegram_client.test_profile_media_actions``; here we only assert
WHICH operations trigger it — the two un-batched photo mutations and the explicit
``resync_account_avatar``, never a refused mutation and never the per-file upload.
``avatar_refresh_calls`` is the autouse recorder from conftest.
"""

from __future__ import annotations

import pytest

from schemas.accounts import AccountCreate
from schemas.profile_media import (
    AccountProfilePhotoRemove,
    AccountProfilePhotoSetMain,
    AccountProfilePhotoUpload,
)
from schemas.telegram_actions import ActionResult, ActionStatus
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    add_account,
    remove_account_profile_photo,
    resync_account_avatar,
    set_account_main_profile_photo,
    set_account_profile_photo,
)

_PHOTO_UPLOAD = AccountProfilePhotoUpload(
    account_id="acc-avatar",
    filename="photo.jpg",
    content=b"jpg-bytes",
)
_PHOTO_REMOVE = AccountProfilePhotoRemove(
    account_id="acc-avatar",
    photo_id=42,
    access_hash=7,
    file_reference=b"\x01",
)
_PHOTO_SET_MAIN = AccountProfilePhotoSetMain(
    account_id="acc-avatar",
    photo_id=42,
    access_hash=7,
    file_reference=b"\x01",
)


def _patch_execute(monkeypatch: pytest.MonkeyPatch, *, status: ActionStatus) -> None:
    async def fake_execute(account_id: str, action: object) -> ActionResult:
        return ActionResult(
            status=status,
            action_type=getattr(action, "action_type", "unknown"),
            account_id=account_id,
            error_message="boom" if status == "failed" else None,
        )

    monkeypatch.setattr("services.accounts.media.execute", fake_execute)


@pytest.mark.asyncio
async def test_set_profile_photo_does_not_refresh_the_list_avatar(
    monkeypatch: pytest.MonkeyPatch,
    avatar_refresh_calls: list[str],
) -> None:
    """The upload takes one file per call; the batch re-syncs once at the end.

    Refreshing per upload spent a ``get_me`` + thumb download on every photo of a
    batch and all but the last were immediately superseded — against the FLOOD_WAIT
    budget the SPA's sequential upload exists to protect.
    """
    _patch_execute(monkeypatch, status="ok")

    await set_account_profile_photo(_PHOTO_UPLOAD)

    assert avatar_refresh_calls == []


@pytest.mark.asyncio
async def test_resync_avatar_refreshes_once_and_returns_the_fresh_row(
    avatar_refresh_calls: list[str],
) -> None:
    await add_account(AccountCreate(account_id="acc-avatar"))

    account = await resync_account_avatar("acc-avatar")

    assert avatar_refresh_calls == ["acc-avatar"]
    assert account.account_id == "acc-avatar"


@pytest.mark.asyncio
async def test_resync_avatar_for_an_unknown_account_spends_no_rpc(
    avatar_refresh_calls: list[str],
) -> None:
    """The 404 guard runs before the refresh, so a bad id costs no Telegram call."""
    with pytest.raises(AccountNotFoundError):
        await resync_account_avatar("never-existed")

    assert avatar_refresh_calls == []


@pytest.mark.asyncio
async def test_remove_profile_photo_refreshes_list_avatar(
    monkeypatch: pytest.MonkeyPatch,
    avatar_refresh_calls: list[str],
) -> None:
    _patch_execute(monkeypatch, status="ok")

    await remove_account_profile_photo(_PHOTO_REMOVE)

    assert avatar_refresh_calls == ["acc-avatar"]


@pytest.mark.asyncio
async def test_set_main_profile_photo_refreshes_list_avatar(
    monkeypatch: pytest.MonkeyPatch,
    avatar_refresh_calls: list[str],
) -> None:
    _patch_execute(monkeypatch, status="ok")

    await set_account_main_profile_photo(_PHOTO_SET_MAIN)

    assert avatar_refresh_calls == ["acc-avatar"]


@pytest.mark.asyncio
async def test_failed_photo_mutation_skips_avatar_refresh(
    monkeypatch: pytest.MonkeyPatch,
    avatar_refresh_calls: list[str],
) -> None:
    """A refused mutation raises before the refresh — no pointless RPC."""
    _patch_execute(monkeypatch, status="failed")

    with pytest.raises(AccountActionError):
        await remove_account_profile_photo(_PHOTO_REMOVE)

    assert avatar_refresh_calls == []
