"""Music tab builder — extracted from ``_profile_dialog`` for file-size budget."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._profile_dialog_footer import _TabFooter
from features.accounts._profile_dialog_render import _apply_optimistic_music
from features.accounts._table import _service_error_label
from schemas.profile_media import AccountProfileMusicUpload
from services.accounts import add_account_profile_music

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments

    from features.accounts._profile_dialog_common import _DialogRefs


@dataclass(slots=True)
class _MusicTabForm:
    """Widget handles + staged-upload buffer the apply handler needs."""

    upload: ui.upload
    title: ui.input
    performer: ui.input
    staged: dict[str, object]


def build_music_tab(  # pragma: no cover - NiceGUI builder
    account_id: str,
    refs: _DialogRefs,
    load_and_apply: Callable[..., Awaitable[None]],
) -> None:
    form = _build_music_form(
        refs,
        lambda: footer.mark_dirty(),  # noqa: PLW0108 — closure binds after footer is assigned
    )

    async def _on_apply() -> None:
        await _apply_music(account_id, refs, form, load_and_apply)

    def _on_cancel() -> None:
        form.upload.reset()
        form.title.value = ""
        form.performer.value = ""
        form.staged["name"] = None
        form.staged["bytes"] = None

    footer = _TabFooter(apply=_on_apply, cancel=_on_cancel)


def _build_music_form(  # pragma: no cover - UI assembly
    refs: _DialogRefs,
    mark_dirty: Callable[[], None],
) -> _MusicTabForm:
    staged: dict[str, object] = {"name": None, "bytes": None}

    async def _on_file_uploaded(event: UploadEventArguments) -> None:
        staged["name"] = event.file.name
        staged["bytes"] = await event.file.read()
        mark_dirty()

    upload = (
        ui.upload(
            label="Выбрать музыку",
            multiple=False,
            max_file_size=30_000_000,
            auto_upload=True,
            on_upload=_on_file_uploaded,
            on_rejected=lambda _e: ui.notify(
                "Музыка отклонена. Проверь: размер ≤ 30 МБ, формат — MP3 или M4A.",
                type="warning",
                timeout=8000,
            ),
        )
        .props('accept=".mp3,.m4a" hide-upload-btn flat bordered')
        .classes("w-full")
    )

    title = ui.input("Название").props("dense outlined clearable").classes("w-full")
    title.tooltip("Если оставить пустым — используем имя файла без расширения")
    performer = ui.input("Исполнитель").props("dense outlined clearable").classes("w-full")

    ui.separator().classes("q-mt-md")
    ui.label("Текущая музыка").classes("text-sm text-grey-8 q-mt-sm")
    refs.music_section = ui.column().classes("w-full gap-2")
    with refs.music_section:
        refs.music_list_container = ui.element("div").classes("w-full")
    return _MusicTabForm(upload=upload, title=title, performer=performer, staged=staged)


async def _apply_music(  # pragma: no cover - click handler
    account_id: str,
    refs: _DialogRefs,
    form: _MusicTabForm,
    load_and_apply: Callable[..., Awaitable[None]],
) -> None:
    name = form.staged["name"]
    content = form.staged["bytes"]
    if not isinstance(name, str) or not isinstance(content, (bytes, bytearray)):
        return
    # Blank Название back-fills to the file stem so Telegram shows the upload
    # filename instead of its own "Audio" placeholder.
    title = (form.title.value or "").strip() or Path(name).stem or None
    performer = (form.performer.value or "").strip() or None
    try:
        await add_account_profile_music(
            AccountProfileMusicUpload(
                account_id=account_id,
                filename=name,
                content=bytes(content),
                title=title,
                performer=performer,
            ),
        )
    except ValueError as exc:
        ui.notify(_service_error_label(str(exc)), type="negative")
        return
    ui.notify("Музыка профиля добавлена", type="positive")
    _apply_optimistic_music(refs, title=title, performer=performer, filename=name)
    form.upload.reset()
    form.title.value = ""
    form.performer.value = ""
    form.staged["name"] = None
    form.staged["bytes"] = None
    await load_and_apply(account_id, refs, force_refresh=True)
