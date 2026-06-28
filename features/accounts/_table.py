"""Accounts table — error-label translation, username diffing, and event glue.

The column defs / cell templates and the pure row-derivation helpers live in
``_table_cells`` (split out to keep each module under the size gate); the
public names are re-exported below so existing call sites keep importing them
from ``features.accounts._table``. The error-label / event helpers here are
pure and unit-tested (``tests/features/test_accounts_helpers.py``).
"""

from __future__ import annotations

from typing import Literal, cast

# Re-exported for ``_table_section`` (column defs + cell templates) and
# ``_controller`` (``_to_table_row`` / ``_account_status_label``) — see
# ``_table_cells`` for the implementations.
from features.accounts._table_cells import (
    _ACTIONS_TEMPLATE,
    _DEVICE_TEMPLATE,
    _PROXY_TEMPLATE,
    _STATUS_BADGE_TEMPLATE,
    _TABLE_COLUMNS,
    _TELEGRAM_TEMPLATE,
    _account_status_label,
    _to_table_row,
)

__all__ = [
    "_ACTIONS_TEMPLATE",
    "_DEVICE_TEMPLATE",
    "_NOTIFY_TYPE_BY_HEALTH",
    "_PROXY_TEMPLATE",
    "_STATUS_BADGE_TEMPLATE",
    "_TABLE_COLUMNS",
    "_TELEGRAM_TEMPLATE",
    "_account_id_from_event",
    "_account_status_label",
    "_remember_selection",
    "_row_from_event",
    "_service_error_label",
    "_to_table_row",
    "_username_update_value",
]

_NOTIFY_TYPE_BY_HEALTH: dict[str, Literal["positive", "warning", "negative"]] = {
    "ok": "positive",
    "warn": "warning",
    "fail": "negative",
}


def _username_update_value(raw: str, snapshot_username: str | None) -> str | None:
    """Return the username to submit, or ``None`` to skip the update entirely.

    Telegram's ``UpdateUsernameRequest`` raises ``USERNAME_NOT_MODIFIED`` when
    the value is unchanged, so re-sending the current username on a name/bio-only
    edit would fail the whole save (name/bio are written first, then the username
    call errors). Returns ``None`` when the cleaned input matches the loaded
    snapshot — the caller then omits the username from the action so the
    gateway's ``if username is not None`` guard skips it. A deliberate clear
    (snapshot had a username, field now blank) returns ``""`` and goes through.
    """
    cleaned = raw.strip().removeprefix("@")
    if cleaned == (snapshot_username or ""):
        return None
    return cleaned


# Status-code → human-readable RU label for tdata-import failures. The keys
# match the ``TdataConvertStatus`` values returned by ``core.tdata_import``.
# Wording follows the 2026 best-practice categories surfaced by the research
# pass: never leak a raw status code or English exception to the operator,
# always include the concrete next step they should take.
_TDATA_STATUS_RU: dict[str, str] = {
    "invalid_zip": (
        "Архив повреждён или это не ZIP. Закройте Telegram Desktop, "
        "заархивируйте папку tdata заново и загрузите."
    ),
    "too_many_files": (
        "В архиве слишком много файлов. Заархивируйте только папку tdata, "
        "без вложенных каталогов кэша."
    ),
    "zip_too_large": (
        "Распакованный размер архива превышает лимит. Удалите кэш-файлы "
        "в папке tdata и заархивируйте заново."
    ),
    "unsafe_path": (
        "В архиве найдены подозрительные пути (выход за пределы папки). "
        "Заархивируйте папку tdata заново стандартным архиватором."
    ),
    "symlinks_not_allowed": (
        "В архиве есть символические ссылки — это не поддерживается. "
        "Заархивируйте папку tdata заново, без ссылок."
    ),
    "tdata_not_found": (
        "Структура папки tdata не распознана. Заархивируйте папку «tdata» "
        "целиком (не её содержимое и не родительскую папку)."
    ),
    "no_accounts": (
        "В архиве не найдено ни одного авторизованного аккаунта. "
        "Проверьте, что Telegram Desktop был залогинен при экспорте."
    ),
}


def _tdata_error_label(status: str, detail: str | None = None) -> str:
    """RU error string for a ``TdataConvertResult.status`` (+ optional detail).

    ``conversion_error`` is the broadest category — it covers locked tdata,
    revoked sessions, AUTH_KEY_UNREGISTERED, version mismatches. We surface
    the underlying library message in parens because those errors are
    inherently library-specific and the operator usually needs the original
    text to figure out the next move (e.g. "passcode protected" → unlock
    Telegram Desktop first).
    """
    if status == "conversion_error":
        tail = f" — {detail}" if detail else ""
        return (
            "Не удалось прочитать tdata. Возможные причины: tdata защищён "
            "локальным паролем (снимите его в Telegram Desktop), сессия отозвана, "
            "или версия Telegram Desktop новее поддерживаемой." + tail
        )
    if status in _TDATA_STATUS_RU:
        return _TDATA_STATUS_RU[status]
    return f"Импорт tdata не удался: {status}" + (f" — {detail}" if detail else "")


def _service_error_label(message: str) -> str:
    replacements = {
        "Session file is empty": "Файл сессии пустой",
        "Upload a .session file": "Загрузите файл .session",
        "Session file name is empty": "Имя файла сессии пустое",
        "tdata contained no accounts": "В tdata не найдено аккаунтов",
    }
    if message in replacements:
        return replacements[message]
    if message.startswith("Session file is too large"):
        return message.replace("Session file is too large", "Файл сессии слишком большой", 1)
    if message.startswith("tdata import failed:"):
        # ``services.accounts._tdata.import_account_tdata`` formats failures as
        # ``tdata import failed: {status}`` or
        # ``tdata import failed: {status} — {error}`` — translate via the
        # category mapper above.
        tail = message[len("tdata import failed: ") :]
        if " — " in tail:
            status, _, detail = tail.partition(" — ")
            return _tdata_error_label(status.strip(), detail.strip())
        return _tdata_error_label(tail.strip())
    if message.startswith("Proxy not found for account:"):
        return message.replace("Proxy not found for account:", "Прокси не найден для аккаунта:", 1)
    return _media_validation_label(message) or _telegram_error_label(message) or message


def _media_validation_label(message: str) -> str | None:
    """Translate the upload-validation errors from ``services.accounts._uploads``.

    ``_validate_upload`` raises ``"<label> file is too large"`` /
    ``"<label> file is empty"`` / ``"<label> must be one of: <suffixes>"`` with a
    free-form English label ("story image", "profile photo", "profile music").
    Match on the stable English tail so the operator gets RU regardless of label.
    """
    if "file is too large" in message:
        return "Файл слишком большой — уменьшите размер и попробуйте снова"
    if "file is empty" in message:
        return "Файл пустой — выберите другой"
    if "must be one of:" in message:
        allowed = message.split("must be one of:", 1)[1].strip()
        return f"Неподдерживаемый формат файла. Разрешены: {allowed}"
    return None


def _telegram_error_label(message: str) -> str | None:
    """Translate common Telethon/Telegram error messages into actionable RU text.

    Substring-matched because Telethon wraps the raw error code in a longer
    English sentence (often appended with ``(caused by SomeRequest)``); a
    full-string equality table would miss the actual error inside. Returns
    ``None`` when no fragment matches so the caller falls through to the
    raw message — better to show English than to swallow an unknown failure.
    """
    fragments: list[tuple[str, str]] = [
        (
            "photo dimensions are invalid",
            "Telegram не принял размеры фото. Используйте JPG 9:16 (рекомендуется 1080×1920)",
        ),
        (
            "PHOTO_INVALID_DIMENSIONS",
            "Telegram не принял размеры фото. Используйте JPG 9:16 (рекомендуется 1080×1920)",
        ),
        (
            "VIDEO_FILE_INVALID",
            "Видео отклонено сервером. Проверьте, что это валидный MP4 до 60 секунд",
        ),
        (
            "IMAGE_PROCESS_FAILED",
            "Telegram не смог обработать видео. Возможно оно длиннее 60 секунд",
        ),
        (
            "MEDIA_FILE_INVALID",
            "Файл медиа отклонён сервером — попробуйте другой",
        ),
        (
            "MEDIA_TYPE_INVALID",
            "Тип медиа не подходит для сторис",
        ),
        (
            "MEDIA_VIDEO_STORY_MISSING",
            "Видео не подошло для сторис — выберите другое",
        ),
        (
            "MEDIA_INVALID",
            "Медиа отклонено: проверьте формат и размер файла",
        ),
        (
            "FILE_PARTS_INVALID",
            "Файл повреждён при загрузке, попробуйте ещё раз",
        ),
        (
            "PHOTO_SAVE_FILE_INVALID",
            "Telegram не смог сохранить фото. Попробуйте другое изображение",
        ),
        (
            "username is already taken",
            "Этот юзернейм уже занят. Выберите другой",
        ),
        (
            "USERNAME_OCCUPIED",
            "Этот юзернейм уже занят. Выберите другой",
        ),
        (
            "USERNAME_PURCHASE_AVAILABLE",
            "Юзернейм занят (доступен для покупки в Fragment). Выберите другой",
        ),
        (
            "USERNAME_INVALID",
            "Юзернейм не подходит — 5–32 символа, латиница/цифры/_, не начинается с цифры",
        ),
        (
            "USERNAME_NOT_MODIFIED",
            "Этот юзернейм уже стоит на аккаунте",
        ),
        (
            "FIRSTNAME_INVALID",
            "Имя не подходит — слишком длинное или содержит запрещённые символы",
        ),
        (
            "LASTNAME_INVALID",
            "Фамилия не подходит — слишком длинная или содержит запрещённые символы",
        ),
        (
            "ABOUT_TOO_LONG",
            "Описание слишком длинное (лимит — 70 символов)",
        ),
    ]
    lowered = message.lower()
    for needle, translation in fragments:
        if needle.lower() in lowered:
            return translation
    return None


def _remember_selection(selection: list[dict[str, object]], selected_ids: set[str]) -> None:
    selected_ids.clear()
    selected_ids.update(str(row["account_id"]) for row in selection)


def _account_id_from_event(event: object) -> str:
    """Extract the account_id payload from a Quasar custom event arg.

    NiceGUI surfaces the Vue ``$emit`` payload as ``event.args`` (str when only
    one arg was emitted; list when multiple). Be tolerant of both shapes.
    """
    args = getattr(event, "args", event)
    if isinstance(args, list) and args:
        args = args[0]
    return str(args) if args is not None else ""


def _row_from_event(event: object) -> dict[str, object]:
    args = getattr(event, "args", event)
    if isinstance(args, list) and args:
        args = args[0]
    return cast("dict[str, object]", args) if isinstance(args, dict) else {}
