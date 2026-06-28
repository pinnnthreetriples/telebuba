"""Accounts table — column defs, Quasar cell templates, and pure row mapping.

The table column defs and cell templates are static presentation data; the
row-mapping / label helpers (``_to_table_row`` and its avatar/pill/proxy/phone/
device sub-helpers) are pure and unit-tested
(``tests/features/accounts/test_table_derive.py``). Split out of ``_table.py``
to keep each module under the size gate; ``_table.py`` re-exports the public
names so existing call sites import from the same place.
"""

from __future__ import annotations


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
    _col("telegram", "Телефон", "telegram"),
    _col("status", "Статус", "status"),
    _col("proxy", "Прокси", "proxy"),
    _col("device", "Устройство", "device"),
    _col("last_checked", "Проверен", "last_checked"),
    _col("actions", "Действия", "account_id", sortable=False, align="right"),
]
# Columns follow design-spec §C.1.4 (Телефон · Статус · Прокси · Устройство ·
# Действия). The spec's "Trust" column is intentionally NOT rendered: the
# Trust Score lives in ``services.trust`` and is not part of ``AccountTableRow``
# — surfacing it would mean changing the schema + service, which is out of the
# accounts-redesign visual scope. "Проверен" (last_checked, real data) takes
# that slot instead. The standalone "Аккаунт"/"Сессия" columns are folded into
# the Телефон cell (avatar + phone + handle).

# Raw ``AccountStatus`` → RU. Single source of truth: the table (via
# ``_to_table_row``) and the check-result toast both translate the raw enum
# through this map, so a status reads identically wherever it appears.
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
}

# Status pill — design-spec §C.1.4 statusMap, keyed on the row's ``health``
# traffic-light (the only status signal in ``AccountTableRow``). bg/color are
# precomputed in ``_to_table_row`` (pure, tested) so the template stays markup.
_STATUS_BADGE_TEMPLATE = """
<q-td :props="props">
  <span class="tb-acc-pill"
        :style="{ background: props.row.status_bg, color: props.row.status_fg }">
    <span class="tb-acc-dot"></span>{{ props.row.status }}
  </span>
</q-td>
"""

# Proxy cell — connection dot (green connected / red failed / grey unknown) +
# flag rect + "{cc} · {type}". Falls back to an em-dash when no proxy is set.
_PROXY_TEMPLATE = """
<q-td :props="props">
  <div v-if="props.row.proxy_host" class="row items-center no-wrap" style="gap:8px">
    <span class="tb-acc-conn" :style="{ background: props.row.proxy_dot }"></span>
    <span v-if="props.row.proxy_flag_url" class="tb-acc-flag"
          :style="{ backgroundImage: props.row.proxy_flag_url }">
    </span>
    <span style="color:#3A3A3A">{{ props.row.proxy_label_short }}</span>
    <q-tooltip class="bg-grey-9 text-body2">
      <div class="text-weight-bold q-mb-xs">{{ props.row.proxy }}</div>
      <div v-if="props.row.proxy_country_name">{{ props.row.proxy_country_name }}</div>
      <div v-if="props.row.proxy_exit_ip">IP: {{ props.row.proxy_exit_ip }}</div>
      <div v-if="props.row.proxy_last_error" class="text-negative q-mt-xs">
        {{ props.row.proxy_last_error }}
      </div>
    </q-tooltip>
  </div>
  <span v-else style="color:#9A9893">—</span>
</q-td>
"""

# Телефон cell — status-coloured mono avatar (last 2 phone digits) + phone on
# top, @handle below. ``avatar_initials`` / ``avatar_class`` / ``phone_display``
# / ``handle_display`` are precomputed in ``_to_table_row``.
_TELEGRAM_TEMPLATE = """
<q-td :props="props">
  <div class="row items-center no-wrap" style="gap:11px">
    <span class="tb-acc-av" :class="props.row.avatar_class">
      {{ props.row.avatar_initials }}
    </span>
    <div class="column" style="line-height:1.3">
      <span class="tb-acc-phone">{{ props.row.phone_display }}</span>
      <span v-if="props.row.handle_display" class="tb-acc-handle">
        {{ props.row.handle_display }}
      </span>
    </div>
  </div>
</q-td>
"""

# Устройство cell — "{model} · {os}" in muted grey (spec §C.1.4).
_DEVICE_TEMPLATE = """
<q-td :props="props">
  <span style="color:#74726E">{{ props.row.device_display }}</span>
</q-td>
"""

# Действия cell — three round 30×30 buttons: alive-check / edit profile /
# delete (spec §C.1.4). Plain HTML buttons styled via ``.tb-acc-act`` so the
# idle/hover states match the design exactly; clicks re-emit the same Vue
# events the controller already listens for, so the wiring is unchanged.
_ACTIONS_TEMPLATE = """
<q-td :props="props">
  <div class="row items-center justify-end no-wrap" style="gap:6px">
    <button class="tb-acc-act" title="Проверить, живой ли аккаунт"
            @click.stop="() => $parent.$emit('check_one', props.row.account_id)">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
    </button>
    <button class="tb-acc-act tb-acc-act-edit" title="Редактировать профиль"
            @click.stop="() => $parent.$emit('edit_profile', props.row)">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>
    </button>
    <button class="tb-acc-act tb-acc-act-edit" title="Настройки прокси"
            @click.stop="() => $parent.$emit('edit_proxy', props.row)">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2 12h6m8 0h6"/><circle cx="12" cy="12" r="3.2"/>
        <path d="M12 2v3m0 14v3"/></svg>
    </button>
    <button class="tb-acc-act tb-acc-act-del" title="Удалить аккаунт"
            @click.stop="() => $parent.$emit('delete_account', props.row)">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 6h18"/>
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/>
        <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
    </button>
  </div>
</q-td>
"""


def _to_table_row(row: dict[str, object]) -> dict[str, object]:
    """Translate a raw ``AccountTableRow`` dump into the shape the cell templates read.

    Beyond the status/last-checked translation, this precomputes the design-spec
    §C.1.4 display fields (mono-avatar initials + colour class, status-pill
    colours, parsed phone/handle, proxy dot/flag/short-label, device line) so the
    Vue cell templates stay pure markup. All derivations are pure and unit-tested.
    """
    translated = dict(row)
    status = str(translated.get("status") or "")
    health = str(translated.get("health") or "warn")
    translated["status"] = _account_status_label(status)
    if translated.get("last_checked") == "never":
        translated["last_checked"] = "никогда"

    phone, handle = _phone_and_handle(str(translated.get("telegram") or ""))
    translated["phone_display"] = phone
    translated["handle_display"] = handle
    translated["avatar_initials"] = _avatar_initials(phone)
    translated["avatar_class"] = _health_avatar_class(health)
    pill_bg, pill_fg = _health_pill_colors(health)
    translated["status_bg"] = pill_bg
    translated["status_fg"] = pill_fg
    translated["device_display"] = _device_display(str(translated.get("device") or ""))
    translated["proxy_dot"] = _proxy_dot_color(translated.get("proxy_status"))
    translated["proxy_flag_url"] = _proxy_flag_url(translated.get("proxy_country_code"))
    translated["proxy_label_short"] = _proxy_label_short(translated)
    return translated


def _account_status_label(status: str) -> str:
    return _STATUS_LABEL_RU.get(status, status.replace("_", " "))


# health → mono-avatar class (design-spec monoMap, mapped onto the coarse
# traffic-light the table actually carries): ok→active blue, fail→banned red,
# warn→code grey.
_HEALTH_AVATAR_CLASS = {
    "ok": "tb-acc-av-active",
    "fail": "tb-acc-av-banned",
    "warn": "tb-acc-av-code",
}
_COUNTRY_CODE_LEN = 2
# health → status-pill (bg, fg) from design-spec statusMap.
_HEALTH_PILL_COLORS = {
    "ok": ("#DDF7E9", "#12A150"),
    "fail": ("#FDE6E2", "#E5372A"),
    "warn": ("#FFF0D2", "#E08700"),
}
# proxy_status → connection-dot colour (spec §C.1.4: green connected / red not).
_PROXY_DOT_COLORS = {"tcp_working": "#2E9E64", "failed": "#C0473F"}


def _health_avatar_class(health: str) -> str:
    return _HEALTH_AVATAR_CLASS.get(health, "tb-acc-av-code")


def _health_pill_colors(health: str) -> tuple[str, str]:
    return _HEALTH_PILL_COLORS.get(health, _HEALTH_PILL_COLORS["warn"])


def _proxy_dot_color(proxy_status: object) -> str:
    return _PROXY_DOT_COLORS.get(str(proxy_status or ""), "#C9C9CE")


def _proxy_flag_cc(country_code: object) -> str:
    """Lowercased 2-letter code for the flagcdn URL, or '' when unknown."""
    code = str(country_code or "").strip().lower()
    return code if len(code) == _COUNTRY_CODE_LEN and code.isalpha() else ""


def _proxy_flag_url(country_code: object) -> str:
    """``url(...)`` CSS value for the proxy flag, or '' when the code is unknown."""
    cc = _proxy_flag_cc(country_code)
    return f"url(https://flagcdn.com/{cc}.svg)" if cc else ""


def _proxy_label_short(row: dict[str, object]) -> str:
    """Build the "{CC} · {TYPE}" proxy-cell label, falling back gracefully.

    Mirrors the spec's "{cc} · {ptype}" text. Uses the country code when known,
    else the proxy type alone, else an em-dash — never an empty string so the
    cell always reads as "has a proxy".
    """
    cc = str(row.get("proxy_country_code") or "").strip().upper()
    ptype = str(row.get("proxy_type") or "").strip().upper()
    parts = [part for part in (cc, ptype) if part]
    return " · ".join(parts) if parts else "—"


def _avatar_initials(phone: str) -> str:
    """Last two digits of the phone — the design's mono-avatar text.

    Falls back to '?' when the phone has no digits (e.g. an account imported
    without a phone number yet).
    """
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits[-2:] if digits else "?"


def _phone_and_handle(telegram: str) -> tuple[str, str]:
    """Split the service's "Name | @handle | phone" line into (phone, handle).

    ``services.accounts._table._telegram_label`` joins up to three parts with
    " | ": the display name, the "@username", and the phone — any of which may
    be absent. The redesigned cell shows the phone as the primary line and the
    handle beneath it, so we pull those two back out: the phone is the part
    that starts with "+" (or the last numeric-looking part), the handle the
    part starting with "@". When no phone part exists we fall back to the first
    non-handle segment so the row never renders blank.
    """
    parts = [part.strip() for part in telegram.split(" | ") if part.strip()]
    if not parts or parts == ["-"]:
        return "—", ""
    handle = next((p for p in parts if p.startswith("@")), "")
    phone = next((p for p in parts if p.startswith("+")), "")
    if not phone:
        phone = next((p for p in parts if p != handle), parts[0])
    return phone or "—", handle


def _device_display(device: str) -> str:
    """Render the service's pipe-joined device string as "model · os · app"."""
    parts = [part.strip() for part in device.split(" | ") if part.strip()]
    return " · ".join(parts) if parts and parts != ["-"] else "—"
