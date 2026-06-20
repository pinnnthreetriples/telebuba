from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from features.accounts import _profile_dialog_photos as photos
from features.accounts import _profile_dialog_render as render
from features.accounts._proxy_dialog import (
    _proxy_dialog_error,
    _proxy_dialog_geo,
    _proxy_dialog_status,
    _proxy_port_value,
)
from features.accounts._table import (
    _account_id_from_event,
    _account_status_label,
    _remember_selection,
    _row_from_event,
    _service_error_label,
    _to_table_row,
)
from schemas.accounts import AccountProfileSnapshot
from schemas.telegram_profile_snapshot import TelegramProfilePhoto

if TYPE_CHECKING:
    import pytest


def test_remember_selection_replaces_selected_ids() -> None:
    selected = {"old"}

    _remember_selection([{"account_id": "acc-1"}, {"account_id": "acc-2"}], selected)

    assert selected == {"acc-1", "acc-2"}


def test_account_id_from_event_accepts_raw_and_nicegui_args() -> None:
    assert _account_id_from_event("acc-1") == "acc-1"
    assert _account_id_from_event(SimpleNamespace(args=["acc-2"])) == "acc-2"
    assert _account_id_from_event(SimpleNamespace(args=None)) == ""


def test_row_from_event_accepts_dict_payload() -> None:
    row = {"account_id": "acc-1"}

    assert _row_from_event(SimpleNamespace(args=[row])) == row
    assert _row_from_event("not-a-row") == {}


def test_proxy_port_value_defaults_to_socks_port() -> None:
    assert _proxy_port_value({"proxy_port": 8000}) == 8000
    assert _proxy_port_value({}) == 1080


def test_proxy_dialog_helpers_render_route_status_and_error() -> None:
    row = {
        "proxy_status": "tcp_working",
        "proxy_last_checked_at": "2026-06-11T12:00:00+00:00",
        "proxy_country_name": "Netherlands",
        "proxy_country_code": "NL",
        "proxy_exit_ip": "45.130.253.155",
        "proxy_last_error": "connection refused",
    }

    assert _proxy_dialog_status(row) == "Статус: работает | проверено 2026-06-11T12:00:00+00:00"
    assert _proxy_dialog_geo(row) == "Маршрут: Netherlands | NL | 45.130.253.155"
    assert _proxy_dialog_error(row) == "Ошибка: connection refused"


def test_proxy_dialog_helpers_render_empty_state() -> None:
    assert _proxy_dialog_status({}) == "Статус: не проверен"
    assert _proxy_dialog_geo({}) == "Маршрут: страна/IP пока неизвестны"
    assert _proxy_dialog_error({}) == ""


def test_account_status_label_translates_known_and_falls_back() -> None:
    assert _account_status_label("alive") == "Живой"
    assert _account_status_label("flood_wait") == "FloodWait"
    # Unknown status: humanised passthrough (underscores → spaces).
    assert _account_status_label("brand_new_state") == "brand new state"


def test_to_table_row_translates_status_and_never() -> None:
    row: dict[str, object] = {"status": "alive", "last_checked": "never", "account_id": "acc-1"}

    translated = _to_table_row(row)

    assert translated["status"] == "Живой"
    assert translated["last_checked"] == "никогда"
    assert translated["account_id"] == "acc-1"  # other fields preserved
    assert row["status"] == "alive"  # input not mutated


def test_to_table_row_keeps_real_last_checked() -> None:
    row: dict[str, object] = {"status": "new", "last_checked": "5m ago"}
    translated = _to_table_row(row)

    assert translated["status"] == "Новый"
    assert translated["last_checked"] == "5m ago"


def test_service_error_label_translates_exact_and_prefixed_messages() -> None:
    assert _service_error_label("Session file is empty") == "Файл сессии пустой"
    assert (
        _service_error_label("Proxy not found for account: acc-1")
        == "Прокси не найден для аккаунта: acc-1"
    )
    assert _service_error_label("Session file is too large (5MB)").startswith(
        "Файл сессии слишком большой",
    )
    # Unmapped message passes through unchanged.
    assert _service_error_label("something else") == "something else"


def test_service_error_label_translates_telegram_story_errors() -> None:
    """Telegram error codes survive Telethon's English wrapping.

    Telethon wraps the raw error code in a longer sentence (often appended
    with ``(caused by SomeRequest)``) — the substring matcher has to find
    the code inside that trailer instead of relying on full-string equality.
    """
    raw = (
        "The photo dimensions are invalid (hint: `pip install pillow` for "
        "`send_file` to resize images) (caused by SendStoryRequest)"
    )
    translated = _service_error_label(raw)
    assert translated.startswith("Telegram не принял размеры фото")
    assert "1080×1920" in translated

    code_only = _service_error_label("PHOTO_INVALID_DIMENSIONS (caused by SendStoryRequest)")
    assert code_only.startswith("Telegram не принял размеры фото")

    video = _service_error_label("VIDEO_FILE_INVALID (caused by SendStoryRequest)")
    assert "Видео отклонено" in video
    assert "60" in video

    long_video = _service_error_label("IMAGE_PROCESS_FAILED (caused by SendStoryRequest)")
    assert "60" in long_video

    media = _service_error_label("MEDIA_INVALID (caused by SaveMusicRequest)")
    assert "Медиа отклонено" in media

    parts = _service_error_label("FILE_PARTS_INVALID")
    assert "повреждён" in parts


def test_service_error_label_unknown_tdata_status_keeps_status_visible() -> None:
    # An unknown status code must still be readable — we don't want to hide
    # the code, because future telemetry will need it to debug.
    assert _service_error_label("tdata import failed: boom") == "Импорт tdata не удался: boom"


def test_service_error_label_maps_known_tdata_statuses_to_actionable_ru() -> None:
    # Each documented ``TdataConvertStatus`` value resolves to an actionable
    # RU label that tells the operator what to fix.
    invalid_zip = _service_error_label("tdata import failed: invalid_zip")
    assert "Архив повреждён" in invalid_zip
    assert "заархивируйте" in invalid_zip.lower()

    tdata_not_found = _service_error_label("tdata import failed: tdata_not_found")
    assert "tdata" in tdata_not_found
    assert "целиком" in tdata_not_found

    no_accounts = _service_error_label("tdata import failed: no_accounts")
    assert "не найдено" in no_accounts.lower()


def test_service_error_label_conversion_error_includes_library_detail() -> None:
    # ``conversion_error`` carries an underlying library message — the operator
    # usually needs that text to figure out the next move (locked tdata,
    # revoked session, version mismatch).
    msg = _service_error_label(
        "tdata import failed: conversion_error — TDesktop load failed: TDataBadDecryptKey",
    )
    assert "Не удалось прочитать tdata" in msg
    assert "TDataBadDecryptKey" in msg
    assert "паролем" in msg or "сессия отозвана" in msg


def test_optimistic_avatar_noop_on_dead_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stale-client guard must short-circuit before touching NiceGUI elements.

    Otherwise a fetch landing after dialog hide / WS drop raises
    "Client has been deleted" warnings on detached elements.
    """
    rendered: list[str] = []
    monkeypatch.setattr(
        render,
        "_render_header",
        lambda _refs, _snap: rendered.append("header"),
    )
    monkeypatch.setattr(
        photos,
        "render_photos_grid",
        lambda _refs, _snap: rendered.append("photo"),
    )

    refs = render._DialogRefs()
    refs.closed = False
    refs.client_id = "dead-client-id"
    refs.current_snapshot = AccountProfileSnapshot(account_id="acc")
    render._DEAD_CLIENTS.add("dead-client-id")
    try:
        render._apply_optimistic_avatar(refs, b"new-bytes")
    finally:
        render._DEAD_CLIENTS.discard("dead-client-id")

    assert rendered == [], "dead-client guard must skip render path entirely"
    assert refs.current_snapshot.avatar_bytes is None, "snapshot must not mutate on dead client"


def test_optimistic_story_appends_and_keeps_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Optimistic story upload appends a synthetic-id thumb in input order.

    No Telegram round-trip required.
    """
    monkeypatch.setattr(render, "_render_stories_preview", lambda *_a, **_k: None)
    monkeypatch.setattr(render, "_render_header", lambda *_a, **_k: None)

    refs = render._DialogRefs()
    refs.closed = False
    refs.client_id = "live"
    refs.current_snapshot = AccountProfileSnapshot(account_id="acc")
    # ``stories_container`` is dereferenced as an argument before the mocked
    # render function is called — give it any placeholder.
    refs.stories_container = object()  # ty: ignore[invalid-assignment]

    render._apply_optimistic_story(
        refs,
        story_bytes=b"img-bytes",
        kind="image",
        caption="hi",
    )
    render._apply_optimistic_story(
        refs,
        story_bytes=b"vid-bytes",
        kind="video",
        caption=None,
    )

    assert refs.current_snapshot is not None
    assert [s.kind for s in refs.current_snapshot.stories] == ["image", "video"]
    assert refs.current_snapshot.stories[0].thumb_bytes == b"img-bytes"
    assert refs.current_snapshot.stories[1].thumb_bytes is None  # video has no thumb yet
    assert refs.current_snapshot.stories[0].caption == "hi"


def test_optimistic_music_uses_form_metadata_and_filename_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``SaveMusicRequest`` returns ``bool`` — nothing to trust.

    Track title falls back to the upload filename when the user left the
    form blank.
    """
    monkeypatch.setattr(render, "_render_music_preview", lambda *_a, **_k: None)
    monkeypatch.setattr(render, "_render_header", lambda *_a, **_k: None)

    refs = render._DialogRefs()
    refs.closed = False
    refs.client_id = "live"
    refs.current_snapshot = AccountProfileSnapshot(account_id="acc")

    render._apply_optimistic_music(
        refs,
        title="Memorabilia",
        performer="The Heads",
        filename="memorabilia.mp3",
    )
    render._apply_optimistic_music(
        refs,
        title=None,
        performer=None,
        filename="untagged.mp3",
    )

    assert refs.current_snapshot is not None
    titles = [t.title for t in refs.current_snapshot.music]
    assert titles == ["Memorabilia", "untagged.mp3"]
    assert refs.current_snapshot.music[0].performer == "The Heads"
    assert refs.current_snapshot.music[1].performer is None


def test_optimistic_photo_remove_drops_and_promotes_avatar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting the current avatar must promote the next photo locally.

    Telegram's ``DeletePhotosRequest`` auto-promotes the previous photo to
    current — the optimistic-remove helper mirrors that so the dialog
    header refreshes without a Telegram round-trip.
    """
    monkeypatch.setattr(photos, "render_photos_grid", lambda *_a, **_k: None)
    monkeypatch.setattr(render, "_render_header", lambda *_a, **_k: None)

    current = TelegramProfilePhoto(
        photo_id=111,
        access_hash=1,
        file_reference=b"\x0a",
        date_unix=1_700_000_000,
        thumb_bytes=b"current",
    )
    previous = TelegramProfilePhoto(
        photo_id=222,
        access_hash=2,
        file_reference=b"\x0b",
        date_unix=1_600_000_000,
        thumb_bytes=b"older",
    )
    refs = render._DialogRefs()
    refs.closed = False
    refs.client_id = "live"
    refs.current_snapshot = AccountProfileSnapshot(
        account_id="acc",
        avatar_bytes=b"current",
        photos=[current, previous],
    )

    photos.apply_optimistic_photo_remove(refs, current.photo_id)

    assert refs.current_snapshot is not None
    assert [p.photo_id for p in refs.current_snapshot.photos] == [222]
    assert refs.current_snapshot.avatar_bytes == b"older"


def test_optimistic_photo_remove_noop_on_dead_client() -> None:
    """Dead-client guard short-circuits before mutating UI state."""
    refs = render._DialogRefs()
    refs.closed = False
    refs.client_id = "dead-photo"
    photo = TelegramProfilePhoto(
        photo_id=1,
        access_hash=1,
        file_reference=b"\x01",
        date_unix=0,
        thumb_bytes=None,
    )
    refs.current_snapshot = AccountProfileSnapshot(
        account_id="acc",
        photos=[photo],
    )
    render._DEAD_CLIENTS.add("dead-photo")
    try:
        photos.apply_optimistic_photo_remove(refs, photo.photo_id)
    finally:
        render._DEAD_CLIENTS.discard("dead-photo")

    assert refs.current_snapshot.photos == [photo], "must not mutate on dead client"
