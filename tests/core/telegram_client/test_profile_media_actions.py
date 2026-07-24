"""Profile photo and music write tests for the Telegram gateway."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from telethon.tl.functions.account import (
    SaveMusicRequest,
)
from telethon.tl.functions.photos import (
    DeletePhotosRequest,
    GetUserPhotosRequest,
    UpdateProfilePhotoRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputPhoto, UserProfilePhotoEmpty

from core.db import create_account, fetch_account, update_account_avatar
from core.telegram_client import execute, refresh_account_avatar
from schemas.accounts import AccountCreate
from schemas.telegram_actions import (
    AddProfileMusic,
    RemoveProfileMusic,
    RemoveProfilePhoto,
    SetMainProfilePhoto,
    SetProfilePhoto,
)
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_execute_set_profile_photo_uploads_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def upload_file(self, _file: object, *, file_name: str) -> object:
            assert file_name == "avatar.jpg"
            return MagicMock()

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo",
        SetProfilePhoto(filename="avatar.jpg", content=_jpeg_bytes()),
    )

    assert result.status == "ok"
    assert any(isinstance(req, UploadProfilePhotoRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_set_profile_photo_rejects_undecodable_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Pillow gate refuses a renamed/corrupt image with a stable code."""
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def upload_file(self, _file: object, *, file_name: str) -> object:
            del file_name
            return MagicMock()

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo-bad",
        SetProfilePhoto(filename="avatar.jpg", content=b"not-an-image"),
    )

    assert result.status == "failed"
    assert result.error_message == "profile_photo_invalid"
    assert captured == []


@pytest.mark.asyncio
async def test_execute_add_profile_music_saves_uploaded_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    deleted: list[int] = []

    monkeypatch.setattr(
        "core.telegram_client._media.utils.get_input_document",
        lambda _document: MagicMock(),
    )

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def send_file(self, entity: str, _file: object, **_kwargs: object) -> object:
            assert entity == "me"
            return MagicMock(id=99, document=object())

        async def delete_messages(
            self,
            entity: str,
            message_ids: list[int],
            *,
            revoke: bool,
        ) -> None:
            assert entity == "me"
            assert revoke is True
            deleted.extend(message_ids)

        async def __call__(self, request: object) -> None:
            captured.append(request)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-music",
        AddProfileMusic(filename="track.mp3", content=b"mp3", title="Track"),
    )

    assert result.status == "ok"
    assert deleted == [99]
    assert any(isinstance(req, SaveMusicRequest) for req in captured)


@pytest.mark.asyncio
async def test_execute_remove_profile_music_ok_when_server_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, SaveMusicRequest)
            assert request.unsave is True
            return True

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-music-remove",
        RemoveProfileMusic(file_id=5, access_hash=6, file_reference=b"\x01"),
    )

    assert result.status == "ok"


@pytest.mark.asyncio
async def test_execute_remove_profile_music_errors_when_server_says_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A false saveMusic response means removal did not happen."""

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            assert isinstance(request, SaveMusicRequest)
            return False

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-music-remove-noop",
        RemoveProfileMusic(file_id=5, access_hash=6, file_reference=b"\x01"),
    )

    assert result.status != "ok"
    assert result.error_message == "profile_music_stale_reference"


@pytest.mark.asyncio
async def test_execute_remove_profile_photo_sends_delete_photos_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing one photo must hit ``DeletePhotosRequest`` with the InputPhoto triple.

    Telegram auto-promotes the next photo to current — we don't re-set the
    avatar from the gateway; the optimistic UI mirrors that promotion locally
    and the next ↻ refresh re-syncs.
    """
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            # ``DeletePhotosRequest`` returns the vector of ids it deleted.
            return [4242]

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo-remove",
        RemoveProfilePhoto(
            photo_id=4242,
            access_hash=7,
            file_reference=b"\x01\x02",
        ),
    )

    assert result.status == "ok"
    delete_requests = [req for req in captured if isinstance(req, DeletePhotosRequest)]
    assert len(delete_requests) == 1
    input_photos = delete_requests[0].id
    assert len(input_photos) == 1
    sent = input_photos[0]
    # ``DeletePhotosRequest.id`` is typed as ``InputPhoto | InputPhotoEmpty``;
    # narrow with an isinstance so ty knows the access_hash / file_reference
    # attributes are present.
    assert isinstance(sent, InputPhoto)
    assert sent.id == 4242
    assert sent.access_hash == 7
    assert sent.file_reference == b"\x01\x02"


@pytest.mark.asyncio
async def test_execute_remove_profile_photo_errors_when_telegram_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty delete vector means the photo stayed — surface an error, not success.

    Telegram silently no-ops a stale or unrecognised ``InputPhoto`` (empty
    vector). That must NOT be reported as a removal — the false success is what
    let JS-rounded int64 ids "delete" the same photo over and over.
    """

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, _request: object) -> object:
            # Telegram recognised no photo to delete.
            return []

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-photo-remove-noop",
        RemoveProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status != "ok"
    assert result.error_message == "profile_photo_stale_reference"


# Fresh id triple the fake GetUserPhotos re-resolves — deliberately different
# from the stale snapshot ref the action carries, to prove re-resolution.
_FRESH_ACCESS_HASH = 99
_FRESH_REFERENCE = b"\xaa\xbb"


def _set_main_client(  # noqa: PLR0913 - keyword-only fake configuration
    captured: list[object],
    *,
    target_id: int,
    updated_id: int | None,
    history_ids: list[int] | None = None,
    history_ids_after: list[int] | None = None,
    avatar_ids: tuple[int | None, int | None] = (None, None),
    photo_bytes: bytes | None = b"jpeg-bytes",
) -> object:
    """Build a fake for fresh lookup, download, and profile-photo re-upload."""
    ids = history_ids if history_ids is not None else [target_id]
    ids_after = history_ids_after if history_ids_after is not None else ids
    calls = {"get_user_photos": 0, "get_full_user": 0}

    class FakeClient:
        def __init__(self) -> None:
            self.downloaded: list[object] = []

        async def connect(self) -> None:
            return None

        async def download_media(self, media: object, *, file: object = None) -> bytes | None:
            del file
            self.downloaded.append(media)
            return photo_bytes

        async def upload_file(self, stream: object, *, file_name: str | None = None) -> object:
            del stream, file_name
            return MagicMock(name="uploaded-avatar")

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, GetUserPhotosRequest):
                calls["get_user_photos"] += 1
                seed = ids if calls["get_user_photos"] == 1 else ids_after
                photos = [
                    SimpleNamespace(
                        id=pid,
                        access_hash=_FRESH_ACCESS_HASH,
                        file_reference=_FRESH_REFERENCE,
                    )
                    for pid in seed
                ]
                return SimpleNamespace(photos=photos)
            if isinstance(request, GetFullUserRequest):
                calls["get_full_user"] += 1
                index = 0 if calls["get_full_user"] == 1 else 1
                avatar_id = avatar_ids[index]
                profile_photo = SimpleNamespace(id=avatar_id) if avatar_id is not None else None
                return SimpleNamespace(full_user=SimpleNamespace(profile_photo=profile_photo))
            if isinstance(request, DeletePhotosRequest):
                return [photo.id for photo in request.id if isinstance(photo, InputPhoto)]
            photo = SimpleNamespace(id=updated_id) if updated_id is not None else None
            return SimpleNamespace(photo=photo)

    return FakeClient()


def _patch_id_flow_log(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, str, dict[str, object]]]:
    events: list[tuple[str, str, dict[str, object]]] = []

    async def _fake_log(
        level: str,
        event: str,
        _account_id: str | None = None,
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra or {}))

    monkeypatch.setattr("core.telegram_client._media.log_event", _fake_log)
    return events


@pytest.mark.asyncio
async def test_execute_set_main_photo_reuploads_as_new_and_logs_id_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Making a photo main re-uploads it, keeps history, and logs exact ids."""
    captured: list[object] = []
    big_id = 9_007_199_254_740_993
    old_main = 111
    filler = 222
    events = _patch_id_flow_log(monkeypatch)
    client = _set_main_client(
        captured,
        target_id=big_id,
        updated_id=555,
        history_ids=[old_main, filler, big_id],
        history_ids_after=[555, old_main, filler, big_id],
        avatar_ids=(old_main, 555),
    )
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=big_id, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status == "ok"
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []
    assert [req for req in captured if isinstance(req, UpdateProfilePhotoRequest)] == []
    assert len([req for req in captured if isinstance(req, UploadProfilePhotoRequest)]) == 1
    assert [getattr(media, "id", None) for media in client.downloaded] == [big_id]  # ty: ignore[unresolved-attribute]
    # One history read + one full-user read: the "after" phase no longer
    # re-fetches either purely for the debug log.
    assert len([req for req in captured if isinstance(req, GetUserPhotosRequest)]) == 1
    assert len([req for req in captured if isinstance(req, GetFullUserRequest)]) == 1
    flow = [(event, extra) for _level, event, extra in events]
    assert [event for event, _extra in flow] == [
        "telegram_set_main_id_flow",
        "telegram_set_main_id_flow",
    ]
    before, after = flow[0][1], flow[1][1]
    assert before["phase"] == "before"
    assert before["target_photo_id"] == big_id
    assert before["history_ids"] == [old_main, filler, big_id]
    assert before["current_avatar_id"] == old_main
    assert after["phase"] == "after"
    assert after["target_photo_id"] == big_id
    assert after["promoted_photo_id"] == 555


@pytest.mark.asyncio
async def test_set_main_profile_photo_never_deletes_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The re-upload flow never deletes either the old main or source photo."""
    captured: list[object] = []
    big_id = 9_007_199_254_740_993
    old_main = 111
    _patch_client(
        monkeypatch,
        _set_main_client(
            captured,
            target_id=big_id,
            updated_id=555,
            history_ids=[old_main, big_id],
            history_ids_after=[555, old_main, big_id],
            avatar_ids=(old_main, 555),
        ),
    )

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=big_id, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status == "ok"
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []
    assert len([req for req in captured if isinstance(req, UploadProfilePhotoRequest)]) == 1


@pytest.mark.asyncio
async def test_execute_set_main_photo_raises_when_target_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    client = _set_main_client(
        captured,
        target_id=4242,
        updated_id=4242,
        history_ids=[9999],
    )
    _patch_client(monkeypatch, client)

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status != "ok"
    assert result.error_message == "profile_photo_not_found"
    assert client.downloaded == []  # ty: ignore[unresolved-attribute]
    assert [req for req in captured if isinstance(req, UploadProfilePhotoRequest)] == []
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []


@pytest.mark.asyncio
async def test_execute_set_main_photo_tolerates_bare_server_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    events = _patch_id_flow_log(monkeypatch)
    _patch_client(monkeypatch, _set_main_client(captured, target_id=4242, updated_id=None))

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status == "ok"
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []
    assert len([req for req in captured if isinstance(req, UploadProfilePhotoRequest)]) == 1
    before, after = (extra for _level, _event, extra in events)
    assert before["current_avatar_id"] is None
    assert after["promoted_photo_id"] is None


@pytest.mark.asyncio
async def test_execute_set_main_photo_fails_when_download_returns_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    _patch_client(
        monkeypatch,
        _set_main_client(captured, target_id=4242, updated_id=555, photo_bytes=None),
    )

    result = await execute(
        "acc-photo-main",
        SetMainProfilePhoto(photo_id=4242, access_hash=7, file_reference=b"\x01\x02"),
    )

    assert result.status != "ok"
    assert result.error_message == "profile_photo_download_failed"
    assert [req for req in captured if isinstance(req, UploadProfilePhotoRequest)] == []
    assert [req for req in captured if isinstance(req, DeletePhotosRequest)] == []


def _patch_avatar_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def fake_get_client(_account_id: str) -> object:
        return client

    monkeypatch.setattr("core.telegram_client._media.get_client", fake_get_client)


def _avatar_client(*, photo: object, thumb: bytes | None) -> object:
    class FakeClient:
        async def get_me(self) -> object:
            return SimpleNamespace(id=1, photo=photo)

        async def download_profile_photo(
            self,
            _me: object,
            *,
            file: object = None,
            download_big: bool = True,
        ) -> bytes | None:
            del file, download_big
            return thumb

    return FakeClient()


async def _seeded_account(account_id: str) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await update_account_avatar(account_id, b"old-thumb")


@pytest.mark.asyncio
async def test_refresh_account_avatar_stores_new_thumb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seeded_account("acc-avatar-new")
    before = await fetch_account("acc-avatar-new")
    assert before is not None
    _patch_avatar_client(
        monkeypatch,
        _avatar_client(photo=SimpleNamespace(photo_id=1), thumb=b"fresh-thumb"),
    )

    await refresh_account_avatar("acc-avatar-new")

    account = await fetch_account("acc-avatar-new")
    assert account is not None
    assert account.avatar_etag is not None
    assert account.avatar_etag != before.avatar_etag


@pytest.mark.asyncio
async def test_refresh_account_avatar_clears_when_no_photo_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the last photo must clear the cached list avatar, not keep it."""
    await _seeded_account("acc-avatar-gone")
    _patch_avatar_client(
        monkeypatch,
        _avatar_client(photo=UserProfilePhotoEmpty(), thumb=None),
    )

    await refresh_account_avatar("acc-avatar-gone")

    account = await fetch_account("acc-avatar-gone")
    assert account is not None
    assert account.avatar_etag is None


@pytest.mark.asyncio
async def test_refresh_account_avatar_keeps_cache_on_refused_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Photo exists but the thumb download was refused — keep the cached one."""
    await _seeded_account("acc-avatar-refused")
    before = await fetch_account("acc-avatar-refused")
    assert before is not None
    _patch_avatar_client(
        monkeypatch,
        _avatar_client(photo=SimpleNamespace(photo_id=1), thumb=None),
    )

    await refresh_account_avatar("acc-avatar-refused")

    account = await fetch_account("acc-avatar-refused")
    assert account is not None
    assert account.avatar_etag == before.avatar_etag


@pytest.mark.asyncio
async def test_refresh_account_avatar_swallows_pool_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh is cosmetic — a dead pool must not fail the mutation."""
    await _seeded_account("acc-avatar-dead")

    async def broken_get_client(_account_id: str) -> object:
        msg = "pool down"
        raise ConnectionError(msg)

    monkeypatch.setattr("core.telegram_client._media.get_client", broken_get_client)

    await refresh_account_avatar("acc-avatar-dead")

    account = await fetch_account("acc-avatar-dead")
    assert account is not None
    assert account.avatar_etag is not None
