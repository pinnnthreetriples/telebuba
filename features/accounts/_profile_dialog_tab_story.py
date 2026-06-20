"""Story tab builder — extracted from ``_profile_dialog`` for file-size budget.

The single ``_profile_story_tab`` builder grew past the aislop function-
length limit after privacy presets, optimistic+force-refresh, and the
existing-stories rail landed. Splitting it into a builder + smaller
``_apply``/``_cancel`` helpers keeps each function under the limit and
lets the dialog module stay focused on layout assembly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._profile_dialog_footer import _TabFooter
from features.accounts._profile_dialog_render import _apply_optimistic_story
from features.accounts._table import _service_error_label
from schemas.profile_media import AccountStoryUpload
from services.accounts import post_account_story

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments

    from features.accounts._profile_dialog_common import _DialogRefs
    from schemas.profile_media import StoryMediaKind


@dataclass(slots=True)
class _StoryTabForm:
    """Widget handles + staged-upload buffer the apply handler needs."""

    upload: ui.upload
    privacy: ui.select
    caption: ui.textarea
    protect: ui.checkbox
    staged: dict[str, object]


_VIDEO_SUFFIXES = {".mp4", ".mov"}


def _infer_story_kind(filename: str) -> StoryMediaKind:
    """Image vs video from the file extension.

    The upload ``accept`` filter only admits jpg/png/webp (image) and mp4/mov
    (video), so the extension is authoritative. Inferring it removes the manual
    «Медиа» dropdown that defaulted to image — uploading a video without flipping
    it produced a server rejection and a broken optimistic preview.
    """
    return "video" if Path(filename).suffix.lower() in _VIDEO_SUFFIXES else "image"


def build_story_tab(  # pragma: no cover - NiceGUI builder, exercised in browser
    account_id: str,
    refs: _DialogRefs,
    load_and_apply: Callable[..., Awaitable[None]],
) -> None:
    form = _build_story_form(
        refs,
        lambda: footer.mark_dirty(),  # noqa: PLW0108 — closure binds after footer is assigned
    )

    async def _on_apply() -> bool:
        return await _apply_story(account_id, refs, form, load_and_apply)

    def _on_cancel() -> None:
        form.upload.reset()
        form.caption.value = ""
        form.staged["name"] = None
        form.staged["bytes"] = None

    footer = _TabFooter(apply=_on_apply, cancel=_on_cancel)


def _build_story_form(  # pragma: no cover - UI assembly
    refs: _DialogRefs,
    mark_dirty: Callable[[], None],
) -> _StoryTabForm:
    """Assemble the publish-side widgets and return them packed for ``_apply``."""
    staged: dict[str, object] = {"name": None, "bytes": None}

    async def _on_file_uploaded(event: UploadEventArguments) -> None:
        staged["name"] = event.file.name
        staged["bytes"] = await event.file.read()
        mark_dirty()

    upload = (
        ui.upload(
            label="Выбрать медиа для сторис",
            multiple=False,
            max_file_size=100_000_000,
            auto_upload=True,
            on_upload=_on_file_uploaded,
            on_rejected=lambda _e: ui.notify(
                "Медиа отклонено. Проверь: изображение ≤ 10 МБ, видео ≤ 100 МБ; "
                "форматы — JPG/JPEG/PNG/WebP/MP4/MOV.",
                type="warning",
                timeout=8000,
            ),
        )
        .props('accept=".jpg,.jpeg,.png,.webp,.mp4,.mov" hide-upload-btn flat bordered')
        .classes("w-full")
    )
    ui.label(
        "Изображение: JPG/PNG/WebP 9:16 (рекомендуется 1080×1920), до 10 МБ · "
        "Видео: MP4/MOV — перекодируем в 9:16 до 60 сек, до 100 МБ",
    ).classes("text-xs text-grey-7")

    privacy = (
        ui.select(
            {
                "contacts": "Контакты",
                "close_friends": "Близкие друзья",
                "public": "Публично",
            },
            value="contacts",
            label="Приватность",
        )
        .props("dense outlined")
        .classes("w-full")
    )
    caption = (
        ui.textarea("Подпись")
        .props("dense outlined autogrow maxlength=1024 counter")
        .classes("w-full")
    )
    protect = ui.checkbox("Защитить контент (запрет на пересылку)", value=False)

    ui.separator().classes("q-mt-md")
    ui.label("Текущие сторис").classes("text-sm text-grey-8 q-mt-sm")
    refs.stories_container = ui.element("div").classes("w-full")
    return _StoryTabForm(
        upload=upload,
        privacy=privacy,
        caption=caption,
        protect=protect,
        staged=staged,
    )


async def _apply_story(  # pragma: no cover - click handler
    account_id: str,
    refs: _DialogRefs,
    form: _StoryTabForm,
    load_and_apply: Callable[..., Awaitable[None]],
) -> bool:
    name = form.staged["name"]
    content = form.staged["bytes"]
    if not isinstance(name, str) or not isinstance(content, (bytes, bytearray)):
        return False
    caption = (form.caption.value or "").strip() or None
    kind = _infer_story_kind(name)
    try:
        await post_account_story(
            AccountStoryUpload(
                account_id=account_id,
                filename=name,
                content=bytes(content),
                media_kind=kind,
                caption=caption,
                privacy_preset=form.privacy.value,
                protect_content=bool(form.protect.value),
            ),
        )
    except ValueError as exc:
        ui.notify(_service_error_label(str(exc)), type="negative")
        return False
    ui.notify("Сторис опубликована", type="positive")
    form.upload.reset()
    form.caption.value = ""
    form.staged["name"] = None
    form.staged["bytes"] = None
    # Optimistic update fits image stories (we have raw bytes); video would
    # show an empty placeholder. Force-refresh lands the canonical state in
    # both cases — apply button's loading spinner covers the brief wait.
    if kind == "image":
        _apply_optimistic_story(refs, story_bytes=bytes(content), kind=kind, caption=caption)
    await load_and_apply(account_id, refs, force_refresh=True)
    return True
