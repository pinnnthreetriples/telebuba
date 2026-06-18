"""Accounts dialogs — add / edit-profile / proxy, plus their tested helpers.

The ``_open_*`` builders and per-tab helpers are UI (``pragma: no cover``); the
proxy label/port helpers are pure and unit-tested.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from core.logging import log_event
from features.accounts._table import _service_error_label
from schemas.accounts import (
    AccountCheckRequest,
    AccountProfileUpdateRequest,
    AccountSessionFileImport,
)
from schemas.profile_media import (
    AccountProfileMusicUpload,
    AccountProfilePhotoUpload,
    AccountStoryUpload,
)
from schemas.tdata import TdataConvertRequest
from services.accounts import (
    add_account_profile_music,
    check_account_session,
    import_account_session,
    import_account_tdata,
    post_account_story,
    set_account_profile_photo,
    update_account_profile,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments


async def _check_accounts(account_ids: set[str]) -> None:  # pragma: no cover
    if not account_ids:
        ui.notify("Аккаунты не выбраны", type="warning")
        return
    for account_id in sorted(account_ids):
        await check_account_session(AccountCheckRequest(account_id=account_id))
    ui.notify("Проверка сессий завершена", type="positive")


_STATUS_ERROR_LABEL_CLASSES = "text-xs text-red-600 leading-snug whitespace-pre-line break-words"
_STATUS_PROGRESS_LABEL_CLASSES = (
    "text-xs text-blue-700 leading-snug whitespace-pre-line break-words"
)
# Wall-clock ceiling on the whole tdata→.session→DB pipeline. Big enough for
# the slowest realistic archive (~1 GB extract + opentele2 read), small enough
# that a stuck network call inside opentele2 surfaces to the operator instead
# of leaving the dialog spinning forever.
_TDATA_IMPORT_TIMEOUT_SECONDS = 120


def _build_dialog_status_controls() -> tuple[
    Callable[[str], None],
    Callable[[str], None],
    Callable[[], None],
]:  # pragma: no cover
    """Persistent status row for the add-account dialog.

    Returns ``(show_error, show_progress, hide)`` closures. The row replaces
    the transient toast as the primary feedback channel: a toast disappears
    in five seconds, and on a failed import that was exactly the bug — the
    operator saw nothing on screen and assumed nothing had happened. The
    row stays visible until the next upload starts or the dialog closes.
    """
    status_row = ui.row().classes("w-full items-start gap-2")
    status_row.visible = False
    with status_row:
        status_icon = ui.icon("error").classes("text-red-500 shrink-0 mt-0.5")
        status_label = ui.label("").classes(_STATUS_ERROR_LABEL_CLASSES)

    def show_error(message: str) -> None:
        status_icon.classes(replace="text-red-500 shrink-0 mt-0.5")
        status_label.classes(replace=_STATUS_ERROR_LABEL_CLASSES)
        status_label.text = message
        status_row.visible = True
        status_row.update()
        # Persistent toast so the operator notices even if they look away from
        # the dialog. ``timeout=0`` keeps it on screen until dismissed.
        ui.notify(message, type="negative", timeout=0, close_button="Закрыть")

    def show_progress(message: str) -> None:
        status_icon.classes(replace="text-blue-500 shrink-0 mt-0.5")
        status_label.classes(replace=_STATUS_PROGRESS_LABEL_CLASSES)
        status_label.text = message
        status_row.visible = True
        status_row.update()

    def hide_status() -> None:
        status_row.visible = False
        status_row.update()

    return show_error, show_progress, hide_status


async def _open_add_dialog(refresh: Callable[[], Awaitable[None]]) -> None:  # pragma: no cover
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-96 max-w-full"):
        ui.label("Добавить аккаунт").classes("text-base font-semibold")
        label = ui.input("Отображаемое имя").props("dense outlined")

        show_error, show_progress, hide_status = _build_dialog_status_controls()

        async def handle_session_upload(event: UploadEventArguments) -> None:
            hide_status()
            show_progress("Импортирую .session…")
            try:
                await import_account_session(
                    AccountSessionFileImport(
                        filename=event.file.name,
                        content=await event.file.read(),
                        label=label.value or None,
                    ),
                )
            except ValueError as exc:
                show_error(_service_error_label(str(exc)))
                return
            except Exception as exc:  # noqa: BLE001 — surface anything else to the user instead of swallowing
                await log_event(
                    "ERROR",
                    "account_session_import_failed",
                    extra={
                        "filename": event.file.name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                show_error(f"Не удалось импортировать .session: {type(exc).__name__}: {exc}")
                return
            hide_status()
            dialog.close()
            ui.notify("Аккаунт добавлен", type="positive")
            await refresh()

        async def handle_tdata_upload(event: UploadEventArguments) -> None:
            hide_status()
            show_progress("Распаковываю архив и читаю tdata…")
            # Stream the upload to a temp file rather than reading the entire
            # archive into RAM (1 GB cap × Pydantic copy = ~2 GB peak otherwise).
            tmp = await asyncio.to_thread(_spool_upload_to_tempfile, event.file)
            try:
                # Hard ceiling so a stuck conversion (network call inside
                # opentele2 with no proxy reachable, or a wedged subprocess)
                # surfaces to the operator instead of pinning the dialog at
                # "in progress" forever. The per-step log events in
                # ``core.tdata_import`` will show which step ran last.
                result = await asyncio.wait_for(
                    import_account_tdata(
                        TdataConvertRequest(
                            filename=event.file.name,
                            content_path=tmp,
                            label=label.value or None,
                        ),
                    ),
                    timeout=_TDATA_IMPORT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                await log_event(
                    "ERROR",
                    "account_tdata_import_timeout",
                    extra={
                        "filename": event.file.name,
                        "timeout_seconds": _TDATA_IMPORT_TIMEOUT_SECONDS,
                    },
                )
                show_error(
                    f"Импорт tdata превысил таймаут {_TDATA_IMPORT_TIMEOUT_SECONDS} с. "
                    "Скорее всего opentele2 не может связаться с Telegram "
                    "(нет интернета или прокси). Смотрите страницу «Логи» — "
                    "там видно, на каком шаге зависло.",
                )
                return
            except ValueError as exc:
                show_error(_service_error_label(str(exc)))
                return
            except Exception as exc:  # noqa: BLE001 — see handle_session_upload
                await log_event(
                    "ERROR",
                    "account_tdata_import_failed",
                    extra={
                        "filename": event.file.name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                show_error(
                    f"Не удалось импортировать tdata: {type(exc).__name__}: {exc}",
                )
                return
            finally:
                with suppress(OSError):
                    tmp.unlink()
            hide_status()
            dialog.close()
            ui.notify(
                f"Импортировано аккаунтов из tdata: {len(result.accounts)}",
                type="positive",
            )
            await refresh()

        ui.upload(
            label="Загрузить .session",
            multiple=False,
            max_file_size=20_000_000,
            auto_upload=True,
            on_upload=handle_session_upload,
            on_rejected=lambda _event: show_error(
                "Файл сессии отклонён: нужен .session до 20 МБ",
            ),
        ).props('accept=".session"').classes("w-full")

        ui.label("или").classes("self-center text-xs text-slate-500")

        ui.upload(
            label="Загрузить tdata.zip",
            multiple=False,
            # Telegram Desktop tdata can be hundreds of MB with cached emoji
            # and user_data — generous cap to fit a real archive.
            max_file_size=1_000_000_000,
            auto_upload=True,
            on_upload=handle_tdata_upload,
            on_rejected=lambda _event: show_error(
                "Архив tdata отклонён: нужен .zip до 1 ГБ",
            ),
        ).props('accept=".zip"').classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Отмена")
    dialog.open()


def _spool_upload_to_tempfile(file: Any) -> Path:  # noqa: ANN401 - NiceGUI's FileUpload has no public stable type.
    """Copy a NiceGUI upload into a private temp file by streaming chunks.

    Reading ``event.file`` end-to-end into RAM is what blows up at 1 GB
    uploads. ``shutil.copyfileobj`` does a fixed-buffer stream copy, so peak
    memory stays at the buffer size regardless of archive size. The caller is
    responsible for unlinking the returned path.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="telebuba_tdata_", suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(file, dst)
    except Exception:
        with suppress(OSError):
            Path(tmp_path).unlink()
        raise
    return Path(tmp_path)


def _profile_text_tab(
    account_id: str,
    refresh: Callable[[], Awaitable[None]],
    close: Callable[..., object],
) -> None:  # pragma: no cover
    first_name = ui.input("Имя", value="").props("dense outlined")
    last_name = ui.input("Фамилия", value="").props("dense outlined clearable")
    username = ui.input("Юзернейм", value="").props("dense outlined clearable prefix=@")
    bio = ui.textarea("Описание", value="").props("dense outlined")

    async def save() -> None:
        name = (first_name.value or "").strip()
        if not name:
            ui.notify("Имя обязательно", type="warning")
            return
        try:
            await update_account_profile(
                AccountProfileUpdateRequest(
                    account_id=account_id,
                    first_name=name,
                    last_name=(last_name.value or "").strip(),
                    username=(username.value or "").strip().removeprefix("@"),
                    bio=(bio.value or "").strip(),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Профиль обновлён", type="positive")
        await refresh()

    with ui.row().classes("w-full justify-end gap-2"):
        ui.button(icon="close", color="grey-7", on_click=close).tooltip("Отмена")
        ui.button(icon="save", color="primary", on_click=save).tooltip("Сохранить профиль")


def _profile_photo_tab(account_id: str) -> None:  # pragma: no cover
    async def handle_photo_upload(event: UploadEventArguments) -> None:
        try:
            await set_account_profile_photo(
                AccountProfilePhotoUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Фото профиля обновлено", type="positive")

    ui.upload(
        label="Загрузить фото профиля",
        multiple=False,
        max_file_size=10_000_000,
        auto_upload=True,
        on_upload=handle_photo_upload,
        on_rejected=lambda _event: ui.notify("Фото профиля отклонено", type="warning"),
    ).props('accept=".jpg,.jpeg,.png,.webp"').classes("w-full")


def _profile_story_tab(account_id: str) -> None:  # pragma: no cover
    story_kind = ui.select(
        {"image": "Изображение", "video": "Видео"},
        value="image",
        label="Медиа",
    ).props("dense outlined")
    story_privacy = ui.select(
        {
            "contacts": "Контакты",
            "close_friends": "Близкие друзья",
            "public": "Публично",
        },
        value="contacts",
        label="Приватность",
    ).props("dense outlined")
    story_caption = ui.textarea("Подпись").props("dense outlined")
    protect_story = ui.checkbox("Защитить контент", value=False)

    async def handle_story_upload(event: UploadEventArguments) -> None:
        try:
            await post_account_story(
                AccountStoryUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                    media_kind=story_kind.value,
                    caption=(story_caption.value or "").strip() or None,
                    privacy_preset=story_privacy.value,
                    protect_content=bool(protect_story.value),
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Сторис опубликована", type="positive")

    ui.upload(
        label="Загрузить медиа для сторис",
        multiple=False,
        max_file_size=100_000_000,
        auto_upload=True,
        on_upload=handle_story_upload,
        on_rejected=lambda _event: ui.notify("Медиа для сторис отклонено", type="warning"),
    ).props('accept=".jpg,.jpeg,.png,.webp,.mp4,.mov"').classes("w-full")


def _profile_music_tab(account_id: str) -> None:  # pragma: no cover
    music_title = ui.input("Название").props("dense outlined clearable")
    music_performer = ui.input("Исполнитель").props("dense outlined clearable")

    async def handle_music_upload(event: UploadEventArguments) -> None:
        try:
            await add_account_profile_music(
                AccountProfileMusicUpload(
                    account_id=account_id,
                    filename=event.file.name,
                    content=await event.file.read(),
                    title=(music_title.value or "").strip() or None,
                    performer=(music_performer.value or "").strip() or None,
                ),
            )
        except ValueError as exc:
            ui.notify(_service_error_label(str(exc)), type="negative")
            return
        ui.notify("Музыка профиля добавлена", type="positive")

    ui.upload(
        label="Загрузить музыку",
        multiple=False,
        max_file_size=30_000_000,
        auto_upload=True,
        on_upload=handle_music_upload,
        on_rejected=lambda _event: ui.notify("Музыка отклонена", type="warning"),
    ).props('accept=".mp3,.m4a"').classes("w-full")


async def _open_profile_dialog(
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:  # pragma: no cover
    account_id = str(row["account_id"])
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[560px] max-w-full"):
        ui.label("Редактировать профиль").classes("text-base font-semibold")
        with ui.tabs().classes("w-full") as tabs:
            text_tab = ui.tab("Текст")
            photo_tab = ui.tab("Фото")
            story_tab = ui.tab("Сторис")
            music_tab = ui.tab("Музыка")
        with ui.tab_panels(tabs, value=text_tab).classes("w-full"):
            with ui.tab_panel(text_tab).classes("gap-3"):
                _profile_text_tab(account_id, refresh, dialog.close)
            with ui.tab_panel(photo_tab).classes("gap-3"):
                _profile_photo_tab(account_id)
            with ui.tab_panel(story_tab).classes("gap-3"):
                _profile_story_tab(account_id)
            with ui.tab_panel(music_tab).classes("gap-3"):
                _profile_music_tab(account_id)
    dialog.open()
