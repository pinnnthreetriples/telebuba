"""Accounts table — column defs, Quasar cell templates, row mapping, event glue.

The row-mapping / label / event-extraction helpers are pure and unit-tested
(``tests/features/test_accounts_helpers.py``); the table column defs and cell
templates are static presentation data.
"""

from __future__ import annotations

from typing import Literal, cast


def _col(
    name: str,
    label: str,
    field: str,
    *,
    sortable: bool = True,
    align: str = "left",
) -> dict[str, object]:
    return {"name": name, "label": label, "field": field, "sortable": sortable, "align": align}


_TABLE_COLUMNS = [
    _col("label", "Аккаунт", "label"),
    _col("status", "Статус", "status"),
    _col("telegram", "Telegram", "telegram"),
    _col("device", "Устройство", "device"),
    _col("proxy", "Прокси", "proxy"),
    _col("last_checked", "Проверен", "last_checked"),
    _col("actions", "", "account_id", sortable=False, align="right"),
]
# Сессия column dropped — it usually duplicates ``Аккаунт`` (same identifier)
# and the extra width pushed the row past the dialog edge, forcing a
# horizontal scrollbar. Per-row session info is still exposed in the edit
# dialog and the row payload, just not as its own table column.

_STATUS_LABEL_RU = {
    "new": "Новый",
    "alive": "Живой",
    "unauthorized": "Не авторизован",
    "session_error": "Ошибка сессии",
    "account_error": "Ошибка аккаунта",
    "flood_wait": "FloodWait",
    "network_error": "Ошибка сети",
    "proxy_error": "Ошибка прокси",
    "unknown_error": "Неизвестная ошибка",
    "New": "Новый",
    "Alive": "Живой",
    "Unauthorized": "Не авторизован",
    "Session error": "Ошибка сессии",
    "Account error": "Ошибка аккаунта",
    "Flood wait": "FloodWait",
    "Network error": "Ошибка сети",
    "Proxy error": "Ошибка прокси",
    "Unknown error": "Неизвестная ошибка",
}

_STATUS_BADGE_TEMPLATE = """
<q-td :props="props">
  <q-chip
    :color="{ok: 'positive', warn: 'warning', fail: 'negative'}[props.row.health] || 'grey-5'"
    text-color="white"
    dense
    :label="props.row.status"
  />
</q-td>
"""

_PROXY_TEMPLATE = """
<q-td :props="props">
  <div v-if="props.row.proxy_host" class="column q-gutter-xs">
    <div class="row items-center no-wrap q-gutter-xs">
      <q-chip
        v-if="props.row.proxy_status === 'tcp_working'"
        dense
        square
        color="positive"
        text-color="white"
        label="Работает"
      />
      <q-chip
        v-else-if="props.row.proxy_status === 'failed'"
        dense
        square
        color="negative"
        text-color="white"
        label="Ошибка"
      />
      <q-chip
        v-else
        dense
        square
        color="grey-6"
        text-color="white"
        label="Не проверен"
      />
      <span class="text-weight-medium">{{ props.row.proxy }}</span>
    </div>
    <div
      v-if="props.row.proxy_country_name || props.row.proxy_country_code || props.row.proxy_exit_ip"
      class="text-caption text-grey-7"
    >
      {{ props.row.proxy_country_name || props.row.proxy_country_code || '' }}
      <span v-if="props.row.proxy_exit_ip"> · {{ props.row.proxy_exit_ip }}</span>
    </div>
  </div>
  <q-chip v-else dense outline square color="grey-6" label="Без прокси" />
</q-td>
"""

_ACTIONS_TEMPLATE = """
<q-td :props="props">
  <q-btn
    dense round flat
    icon="manage_accounts"
    color="primary"
    @click="() => $parent.$emit('edit_profile', props.row)"
  >
    <q-tooltip>Редактировать профиль</q-tooltip>
  </q-btn>
  <q-btn
    dense round flat
    icon="vpn_key"
    color="primary"
    @click="() => $parent.$emit('edit_proxy', props.row)"
  >
    <q-tooltip>Настройки прокси</q-tooltip>
  </q-btn>
  <q-btn
    dense round flat
    icon="refresh"
    color="primary"
    @click="() => $parent.$emit('check_one', props.row.account_id)"
  >
    <q-tooltip>Проверить аккаунт</q-tooltip>
  </q-btn>
  <q-btn
    dense round flat
    icon="delete"
    color="negative"
    @click="() => $parent.$emit('delete_account', props.row)"
  >
    <q-tooltip>Удалить аккаунт</q-tooltip>
  </q-btn>
</q-td>
"""

_NOTIFY_TYPE_BY_HEALTH: dict[str, Literal["positive", "warning", "negative"]] = {
    "ok": "positive",
    "warn": "warning",
    "fail": "negative",
}


def _to_table_row(row: dict[str, object]) -> dict[str, object]:
    translated = dict(row)
    status = str(translated.get("status") or "")
    translated["status"] = _account_status_label(status)
    if translated.get("last_checked") == "never":
        translated["last_checked"] = "никогда"
    return translated


def _account_status_label(status: str) -> str:
    return _STATUS_LABEL_RU.get(status, status.replace("_", " "))


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
    return _telegram_error_label(message) or message


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
