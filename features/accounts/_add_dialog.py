"""Add-account dialog — .session and tdata.zip upload handlers.

The two upload handlers are lifted to module level (rather than nested inside
``_open_add_dialog``) so the dialog builder stays under the aislop function
length cap. They share captured state through ``_UploadCtx``.
"""

# ruff: noqa: ANN401 - NiceGUI's FileUpload and the tdata pipeline result have no public stable type.

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nicegui import ui

from core.logging import log_event
from features.accounts._table import _service_error_label
from schemas.accounts import AccountSessionFileImport
from schemas.tdata import TdataConvertRequest
from services.accounts import (
    import_account_session,
    import_account_tdata,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from nicegui.events import UploadEventArguments


_STATUS_ERROR_LABEL_CLASSES = "text-xs text-red-600 leading-snug whitespace-pre-line break-words"
_STATUS_PROGRESS_LABEL_CLASSES = (
    "text-xs text-blue-700 leading-snug whitespace-pre-line break-words"
)
_TDATA_IMPORT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True, slots=True)
class _UploadCtx:
    """State both upload handlers need from the dialog builder."""

    get_label_value: Callable[[], str | None]
    refresh: Callable[[], Awaitable[None]]
    show_error: Callable[[str], None]
    show_progress: Callable[[str], None]
    hide_status: Callable[[], None]
    close_dialog: Callable[[], object]


async def _spool_upload_to_tempfile(file: Any) -> Path:
    """Copy a NiceGUI upload into a private temp file by streaming chunks.

    ``FileUpload.save`` iterates the upload in 1 MB chunks, so peak memory
    stays flat regardless of archive size. The caller is responsible for
    unlinking the returned path.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="telebuba_tdata_", suffix=".zip")
    os.close(fd)
    path = Path(tmp_path)
    try:
        await file.save(path)
    except Exception:
        with suppress(OSError):
            await asyncio.to_thread(path.unlink)
        raise
    return path


async def _run_tdata_pipeline(
    event: UploadEventArguments,
    tmp_holder: list[Path | None],
    label_value: str | None,
) -> Any:
    """Spool the upload + run the import, surfacing every step in logs.

    The ``tmp_holder`` list is a one-slot out-parameter so the outer handler
    can unlink the temp file in its ``finally`` even when this coroutine is
    cancelled by the timeout.
    """
    await log_event(
        "INFO",
        "account_tdata_spool_started",
        extra={"filename": getattr(event.file, "name", "?")},
    )
    tmp = await _spool_upload_to_tempfile(event.file)
    tmp_holder[0] = tmp
    await log_event(
        "INFO",
        "account_tdata_spool_completed",
        extra={
            "filename": getattr(event.file, "name", "?"),
            "tmp_path": str(tmp),
            "tmp_size": tmp.stat().st_size if tmp.exists() else -1,
        },
    )
    return await import_account_tdata(
        TdataConvertRequest(
            filename=event.file.name,
            content_path=tmp,
            label=label_value or None,
        ),
    )


def _build_dialog_status_controls() -> tuple[
    Callable[[str], None],
    Callable[[str], None],
    Callable[[], None],
]:  # pragma: no cover
    """Persistent status row for the add-account dialog.

    Returns ``(show_error, show_progress, hide)`` closures. The row replaces
    the transient toast as the primary feedback channel and stays visible
    until the next upload starts or the dialog closes.
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


async def _handle_session_upload(
    event: UploadEventArguments,
    ctx: _UploadCtx,
) -> None:  # pragma: no cover
    await log_event(
        "INFO",
        "account_session_import_started",
        extra={"filename": getattr(event.file, "name", "?")},
    )
    ctx.hide_status()
    ctx.show_progress("Импортирую .session…")
    try:
        await import_account_session(
            AccountSessionFileImport(
                filename=event.file.name,
                content=await event.file.read(),
                label=ctx.get_label_value(),
            ),
        )
    except ValueError as exc:
        ctx.show_error(_service_error_label(str(exc)))
        return
    except Exception as exc:  # noqa: BLE001 — surface anything else instead of swallowing
        await log_event(
            "ERROR",
            "account_session_import_failed",
            extra={
                "filename": event.file.name,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        ctx.show_error(f"Не удалось импортировать .session: {type(exc).__name__}: {exc}")
        return
    ctx.hide_status()
    ctx.close_dialog()
    ui.notify("Аккаунт добавлен", type="positive")
    await ctx.refresh()


async def _handle_tdata_upload(
    event: UploadEventArguments,
    ctx: _UploadCtx,
) -> None:  # pragma: no cover
    filename = getattr(event.file, "name", "?")
    await log_event(
        "INFO",
        "account_tdata_import_started",
        extra={"filename": filename},
    )
    ctx.hide_status()
    ctx.show_progress("Распаковываю архив и читаю tdata…")
    tmp: Path | None = None
    try:
        result = await asyncio.wait_for(
            _run_tdata_pipeline(event, tmp_holder := [None], ctx.get_label_value()),
            timeout=_TDATA_IMPORT_TIMEOUT_SECONDS,
        )
        tmp = tmp_holder[0]
    except TimeoutError:
        await log_event(
            "ERROR",
            "account_tdata_import_timeout",
            extra={"filename": filename, "timeout_seconds": _TDATA_IMPORT_TIMEOUT_SECONDS},
        )
        ctx.show_error(
            f"Импорт tdata превысил таймаут {_TDATA_IMPORT_TIMEOUT_SECONDS} с. "
            "Скорее всего opentele2 не может связаться с Telegram "
            "(нет интернета или прокси). Смотрите страницу «Логи» — "
            "там видно, на каком шаге зависло.",
        )
        return
    except ValueError as exc:
        ctx.show_error(_service_error_label(str(exc)))
        return
    except Exception as exc:  # noqa: BLE001 — see _handle_session_upload
        await log_event(
            "ERROR",
            "account_tdata_import_failed",
            extra={
                "filename": filename,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        ctx.show_error(f"Не удалось импортировать tdata: {type(exc).__name__}: {exc}")
        return
    finally:
        if tmp is not None:
            with suppress(OSError):
                tmp.unlink()
    ctx.hide_status()
    ctx.close_dialog()
    ui.notify(
        f"Импортировано аккаунтов из tdata: {len(result.accounts)}",
        type="positive",
    )
    await ctx.refresh()


async def _open_add_dialog(  # pragma: no cover
    refresh: Callable[[], Awaitable[None]],
) -> None:
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-96 max-w-full"):
        ui.label("Добавить аккаунт").classes("text-base font-semibold")
        label = ui.input("Отображаемое имя").props("dense outlined")
        show_error, show_progress, hide_status = _build_dialog_status_controls()
        ctx = _UploadCtx(
            get_label_value=lambda: label.value or None,
            refresh=refresh,
            show_error=show_error,
            show_progress=show_progress,
            hide_status=hide_status,
            close_dialog=dialog.close,
        )
        ui.upload(
            label="Загрузить .session",
            multiple=False,
            max_file_size=20_000_000,
            auto_upload=True,
            on_upload=lambda event: _handle_session_upload(event, ctx),
            on_rejected=lambda _event: show_error(
                "Файл сессии отклонён: нужен .session до 20 МБ",
            ),
        ).props('accept=".session"').classes("w-full")

        ui.label("или").classes("self-center text-xs text-slate-500")

        ui.upload(
            label="Загрузить tdata.zip",
            multiple=False,
            max_file_size=1_000_000_000,
            auto_upload=True,
            on_upload=lambda event: _handle_tdata_upload(event, ctx),
            on_rejected=lambda _event: show_error(
                "Архив tdata отклонён: нужен .zip до 1 ГБ",
            ),
        ).props('accept=".zip"').classes("w-full")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(icon="close", color="grey-7", on_click=dialog.close).tooltip("Отмена")
    dialog.open()
