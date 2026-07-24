"""Post-mutation avatar re-sync wiring for the media service.

The refresh itself (pool borrow + thumb download + DB write) is covered in
``tests.core.telegram_client.test_profile_media_actions``; here we only assert
the service calls it after each successful photo mutation and never after a
refused one. ``avatar_refresh_calls`` is the autouse recorder from conftest.
"""

from __future__ import annotations

import pytest

from schemas.profile_media import (
    AccountProfilePhotoRemove,
    AccountProfilePhotoSetMain,
    AccountProfilePhotoUpload,
)
from schemas.telegram_actions import ActionResult, ActionStatus
from services.accounts import (
    AccountActionError,
    remove_account_profile_photo,
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
async def test_set_profile_photo_refreshes_list_avatar(
    monkeypatch: pytest.MonkeyPatch,
    avatar_refresh_calls: list[str],
) -> None:
    _patch_execute(monkeypatch, status="ok")

    await set_account_profile_photo(_PHOTO_UPLOAD)

    assert avatar_refresh_calls == ["acc-avatar"]


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
        await set_account_profile_photo(_PHOTO_UPLOAD)

    assert avatar_refresh_calls == []
