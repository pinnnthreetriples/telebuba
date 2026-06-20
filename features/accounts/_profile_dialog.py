"""Edit-profile dialog entrypoint + per-tab builders.

When the dialog opens the user immediately sees a header strip (avatar + name +
@username + phone) and the four edit tabs. A background task fetches the live
profile snapshot via :func:`services.accounts.fetch_live_account_profile` and
fills in the text inputs, the photo preview, the existing-stories grid, and the
existing-music list. A "↻" button re-fetches bypassing the 5-min TTL cache.

If Telegram refuses the fetch (FloodWait, RPCError) the dialog still opens and
shows the error inline — save/upload paths keep working against the local DB.

Render helpers + ``_DialogRefs`` live in :mod:`_profile_dialog_render` so
neither file blows the aislop size budget.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nicegui import context, ui

from features.accounts._profile_dialog_footer import _TabFooter
from features.accounts._profile_dialog_render import (
    _apply_optimistic_avatar,
    _apply_snapshot,
    _DialogRefs,
    _render_loading_header,
)
from features.accounts._profile_dialog_tab_music import build_music_tab
from features.accounts._profile_dialog_tab_story import build_story_tab
from features.accounts._table import _service_error_label, _username_update_value
from schemas.accounts import AccountProfileUpdateRequest
from schemas.profile_media import AccountProfilePhotoUpload
from services.accounts import (
    fetch_live_account_profile,
    set_account_profile_photo,
    update_account_profile,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments


# Strip Quasar's "done_all" header check and the per-file "done" round check
# that QUploader paints after a file completes — users kept reading them as
# rival "apply" buttons. Inspecting the rendered DOM gave us two precise
# selectors that hit those icons without touching the "+" add-files button
# (which lives in the same header row). Scoped under ``.tb-profile-dialog`` (the
# dialog root class) so it does NOT touch other uploaders in the app, e.g. the
# add-account .session/tdata pickers. ``shared=True`` is required by NiceGUI
# 3.x for module-scope CSS injection.
ui.add_css(
    """
    .tb-profile-dialog .q-uploader__header-content > div > a.q-btn:not(:last-of-type),
    .tb-profile-dialog .q-uploader__file-header .q-btn--round {
        display: none !important;
    }
    /* Hide the empty file-list pane until a file is staged — Quasar paints
       a tall placeholder area there by default which makes the uploader
       feel huge on tabs where only the header drop-zone needs to show. */
    .tb-profile-dialog .q-uploader:not(:has(.q-uploader__file)) .q-uploader__list {
        display: none;
    }
    """,
    shared=True,
)


async def _load_and_apply(
    account_id: str,
    refs: _DialogRefs,
    *,
    force_refresh: bool,
) -> None:
    refs.refresh_button.disable()
    _render_loading_header(refs, account_id)
    snapshot = await fetch_live_account_profile(account_id, force_refresh=force_refresh)
    _apply_snapshot(refs, snapshot)


def _profile_text_tab(
    account_id: str,
    refs: _DialogRefs,
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    refs.first_name = ui.input("Имя", value="").props("dense outlined").classes("w-full")
    refs.last_name = (
        ui.input("Фамилия", value="").props("dense outlined clearable").classes("w-full")
    )
    refs.username = (
        ui.input("Юзернейм", value="").props("dense outlined clearable prefix=@").classes("w-full")
    )
    refs.bio = ui.textarea("Описание", value="").props("dense outlined").classes("w-full")
    refs.first_name.disable()
    refs.last_name.disable()
    refs.username.disable()
    refs.bio.disable()

    def _text_baseline_matches() -> bool:
        snap = refs.current_snapshot
        if snap is None:
            return True
        cleaned_username = (refs.username.value or "").strip().removeprefix("@")
        return (
            (refs.first_name.value or "").strip() == (snap.first_name or "")
            and (refs.last_name.value or "").strip() == (snap.last_name or "")
            and cleaned_username == (snap.username or "")
            and (refs.bio.value or "").strip() == (snap.bio or "")
        )

    async def _apply() -> bool:
        name = (refs.first_name.value or "").strip()
        if not name:
            ui.notify("Имя обязательно", type="warning")
            return False
        snap = refs.current_snapshot
        try:
            await update_account_profile(
                AccountProfileUpdateRequest(
                    account_id=account_id,
                    first_name=name,
                    last_name=(refs.last_name.value or "").strip(),
                    username=_username_update_value(
                        refs.username.value or "",
                        snap.username if snap else None,
                    ),
                    bio=(refs.bio.value or "").strip(),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return False
        ui.notify("Профиль обновлён", type="positive")
        await refresh()
        await _load_and_apply(account_id, refs, force_refresh=True)
        return True

    def _cancel() -> None:
        snap = refs.current_snapshot
        if snap is None:
            return
        refs.first_name.value = snap.first_name or ""
        refs.username.value = snap.username or ""
        refs.last_name.value = snap.last_name or ""
        refs.bio.value = snap.bio or ""

    footer = _TabFooter(apply=_apply, cancel=_cancel)

    def _check_dirty(_event: object = None) -> None:
        if _text_baseline_matches():
            footer.mark_clean()
        else:
            footer.mark_dirty()

    refs.first_name.on_value_change(_check_dirty)
    refs.last_name.on_value_change(_check_dirty)
    refs.username.on_value_change(_check_dirty)
    refs.bio.on_value_change(_check_dirty)


def _profile_photo_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    staged: dict[str, object] = {"name": None, "bytes": None}

    async def _on_file_uploaded(event: UploadEventArguments) -> None:
        staged["name"] = event.file.name
        staged["bytes"] = await event.file.read()
        footer.mark_dirty()

    # Upload widget at top — primary operator task on this tab is publishing
    # a new avatar. Existing-photos rail lives at the bottom as historical
    # context, mirroring the stories tab layout.
    photo_upload = (
        ui.upload(
            label="Выбрать фото профиля",
            multiple=False,
            max_file_size=10_000_000,
            auto_upload=True,
            on_upload=_on_file_uploaded,
            on_rejected=lambda _e: ui.notify(
                "Фото отклонено. Проверь: размер ≤ 10 МБ, формат — JPG/JPEG/PNG/WebP.",
                type="warning",
                timeout=8000,
            ),
        )
        .props('accept=".jpg,.jpeg,.png,.webp" hide-upload-btn flat bordered')
        .classes("w-full")
    )

    ui.separator().classes("q-mt-md")
    ui.label("Текущие фото").classes("text-sm text-grey-8 q-mt-sm")
    refs.photo_preview_container = ui.element("div").classes("w-full")

    async def _apply() -> bool:
        name = staged["name"]
        content = staged["bytes"]
        if not isinstance(name, str) or not isinstance(content, (bytes, bytearray)):
            return False
        try:
            await set_account_profile_photo(
                AccountProfilePhotoUpload(
                    account_id=account_id,
                    filename=name,
                    content=bytes(content),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return False
        ui.notify("Фото профиля обновлено", type="positive")
        # Optimistic update gives instant feedback (we have the raw bytes),
        # then force-refresh pulls canonical state — photo_id, file_reference
        # for future deletion, and Telegram's normalised dimensions.
        _apply_optimistic_avatar(refs, bytes(content))
        photo_upload.reset()
        staged["name"] = None
        staged["bytes"] = None
        await _load_and_apply(account_id, refs, force_refresh=True)
        return True

    def _cancel() -> None:
        photo_upload.reset()
        staged["name"] = None
        staged["bytes"] = None

    footer = _TabFooter(apply=_apply, cancel=_cancel)


def _profile_story_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    build_story_tab(account_id, refs, _load_and_apply)


def _profile_music_tab(account_id: str, refs: _DialogRefs) -> None:  # pragma: no cover
    build_music_tab(account_id, refs, _load_and_apply)


async def _open_profile_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    refs = _DialogRefs()
    refs.account_id = account_id
    refs.current_snapshot = None
    refs.client_id = context.client.id
    refs.closed = False
    with (
        ui.dialog() as dialog,
        ui.column().classes("tb-profile-dialog bg-white p-4 gap-3 w-[640px] max-w-full"),
    ):
        ui.label("Редактировать профиль").classes("text-base font-semibold")
        with ui.row().classes(
            "items-center gap-3 w-full no-wrap border rounded-md p-3 bg-grey-1",
        ):
            refs.avatar_slot = ui.element("div").classes("shrink-0")
            refs.identity_slot = ui.column().classes("gap-0 flex-1 min-w-0")
            with ui.column().classes("items-end gap-1 shrink-0"):
                refs.refresh_button = (
                    ui.button(
                        icon="refresh",
                        color="grey-7",
                        on_click=lambda: _load_and_apply(
                            account_id,
                            refs,
                            force_refresh=True,
                        ),
                    )
                    .props("flat dense round")
                    .tooltip("Обновить с Telegram")
                )
                refs.refresh_button.disable()
                refs.sync_label = ui.label("—").classes("text-[10px] text-grey-6")
        refs.error_banner = ui.label("").classes(
            "text-xs text-negative bg-red-1 rounded px-2 py-1",
        )
        refs.error_banner.set_visibility(False)

        with ui.tabs().classes("w-full") as tabs:
            text_tab = ui.tab("Текст")
            photo_tab = ui.tab("Фото")
            story_tab = ui.tab("Сторис")
            music_tab = ui.tab("Музыка")
        with ui.tab_panels(tabs, value=text_tab).classes("w-full"):
            with ui.tab_panel(text_tab).classes("gap-3"):
                _profile_text_tab(account_id, refs, refresh)
            with ui.tab_panel(photo_tab).classes("gap-3"):
                _profile_photo_tab(account_id, refs)
            with ui.tab_panel(story_tab).classes("gap-3"):
                _profile_story_tab(account_id, refs)
            with ui.tab_panel(music_tab).classes("gap-3"):
                _profile_music_tab(account_id, refs)

    _render_loading_header(refs, account_id)
    refs.initial_load_task = asyncio.create_task(
        _load_and_apply(account_id, refs, force_refresh=False),
    )

    def _on_hide() -> None:
        # Cancel the in-flight fetch AND flip the closed flag so apply paths
        # short-circuit when they land after dialog hide. ``cancel()`` alone
        # is not enough — the apply path has no await points after the fetch,
        # so cancellation can't interrupt it.
        refs.closed = True
        refs.initial_load_task.cancel()

    dialog.on("hide", _on_hide)
    dialog.open()
