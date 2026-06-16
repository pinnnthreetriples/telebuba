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
    _col("session", "Сессия", "session"),
    _col("device", "Устройство", "device"),
    _col("proxy", "Прокси", "proxy"),
    _col("last_checked", "Проверен", "last_checked"),
    _col("actions", "", "account_id", sortable=False, align="right"),
]

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
    icon="refresh"
    color="primary"
    @click="() => $parent.$emit('check_one', props.row.account_id)"
  >
    <q-tooltip>Проверить аккаунт</q-tooltip>
  </q-btn>
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
        return message.replace("tdata import failed:", "Импорт tdata не удался:", 1)
    if message.startswith("Proxy not found for account:"):
        return message.replace("Proxy not found for account:", "Прокси не найден для аккаунта:", 1)
    return message


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
