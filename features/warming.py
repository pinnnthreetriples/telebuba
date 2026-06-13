"""NiceGUI Warming page.

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.warming`` function, and re-renders. No business logic here.

Layout:
- **Settings** — Gemini API key + model only.
- **Features** — on/off toggles for what warming accounts may do (auto-saved).
- **Channels** — add unlimited links/usernames; existing ones shown in a table.
- **Kanban** — drag accounts between *Idle* and *Warming*; dropping into the
  Warming column starts the loop, dropping back into Idle stops it.
- **Activity log** — live, colour-coded (green/amber/red) feed of warming events.

Anti-flicker: the board and the log only re-render when their content actually
changes (a content signature is compared each poll), so an idle page does no DOM
work and does not blink.

Everything below is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually, the logic it calls is unit-tested.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

from nicegui import ui

from schemas.logs import LogFilter
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingSettingsUpdate,
)
from services.logs import load_logs_page
from services.warming import (
    WarmingNotReadyError,
    add_channels,
    load_board,
    remove_channel,
    save_settings,
    start_warming,
    stop_warming,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from schemas.logs import LogEntry
    from schemas.warming import WarmingAccountState, WarmingBoardState, WarmingSettings

_BOARD_POLL_SECONDS = 4.0
_LOG_POLL_SECONDS = 2.0
_LOG_LIMIT = 40

_HEALTH_DOT = {
    "ok": "bg-green-500",
    "warn": "bg-amber-500",
    "fail": "bg-red-500",
    "idle": "bg-slate-400",
}
_STATE_LABEL = {
    "idle": "Простой",
    "active": "Прогрев",
    "sleeping": "Сон",
    "flood_wait": "Flood-ожидание",
    "error": "Ошибка",
}
_STATE_BADGE = {
    "idle": "text-slate-600 bg-slate-100",
    "active": "text-green-700 bg-green-100",
    "sleeping": "text-amber-700 bg-amber-100",
    "flood_wait": "text-amber-800 bg-amber-100",
    "error": "text-red-700 bg-red-100",
}
_LOG_ROW_BORDER = {
    "success": "border-green-500",
    "warning": "border-amber-500",
    "error": "border-red-500",
}

# Readiness reasons are produced (in English) by ``services.warming`` and are
# also written to logs/tests; translate them here at the UI edge only.
_READINESS_REASON_RU = {
    "no proxy": "нет прокси",
    "proxy failed": "прокси не работает",
    "no channels": "нет каналов",
}


def _ru_reason(reason: str) -> str:  # pragma: no cover
    if reason in _READINESS_REASON_RU:
        return _READINESS_REASON_RU[reason]
    if reason.startswith("session "):
        return f"сессия: {reason[len('session ') :]}"
    return reason


_WARMING_CSS = """
@keyframes tb-pulse-ring {
    0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.45); }
    70% { box-shadow: 0 0 0 8px rgba(34,197,94,0); }
    100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
}
.tb-active { animation: tb-pulse-ring 1.8s infinite; }
.tb-dropzone { transition: background-color 0.2s ease, border-color 0.2s ease; }
"""


def register_warming_page() -> None:  # pragma: no cover
    @ui.page("/warming", title="Telebuba — Прогрев")
    async def warming_page() -> None:
        await _render_warming_page()


def _build_header() -> None:  # pragma: no cover
    with (
        ui.row().classes(
            "w-full items-center justify-between px-4 py-2 bg-white "
            "text-slate-950 border-b border-slate-200",
        ),
        ui.row().classes("items-center gap-4"),
    ):
        ui.label("Telebuba").classes("text-lg font-semibold")
        ui.link("Аккаунты", "/").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")
        ui.link("Прогрев", "/warming").classes("text-sm font-medium text-slate-900 no-underline")
        ui.link("Логи", "/logs").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")


async def _render_warming_page() -> None:  # pragma: no cover
    ui.add_head_html(f"<style>{_WARMING_CSS}</style>")
    ui.query("body").classes("bg-slate-50 text-slate-950")
    _build_header()

    drag: dict[str, str | None] = {"account_id": None}

    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-4"):
        ui.label("Прогрев аккаунтов").classes("text-xl font-semibold")
        with ui.row().classes("w-full gap-4 items-start flex-wrap"):
            await _render_config_cards()
            await _render_channels_card()

        _render_how_it_works()

        initial = await load_board()
        holder: dict[str, object] = {"board": initial, "sig": _board_signature(initial)}

        @ui.refreshable
        async def render_board() -> None:
            _render_board(cast("WarmingBoardState", holder["board"]), drag, force_reload)

        async def reload(*, force: bool = False) -> None:
            board = await load_board()
            signature = _board_signature(board)
            # Skip the DOM rebuild when nothing changed — this is what stops the
            # Idle column from blinking every poll.
            if not force and signature == holder["sig"]:
                return
            holder["board"] = board
            holder["sig"] = signature
            render_board.refresh()

        def force_reload() -> asyncio.Task[None]:
            # Returned (not discarded) so the task keeps a strong reference and
            # is not garbage-collected mid-flight.
            return asyncio.create_task(reload(force=True))

        await render_board()
        ui.timer(_BOARD_POLL_SECONDS, reload)

        await _render_activity_log()


def _settings_status(model: str, *, has_key: bool) -> str:  # pragma: no cover
    return f"Модель {model} · AI-ключ {'задан' if has_key else 'нет'}"


def _clamp_hour(value: object) -> int:  # pragma: no cover
    """Coerce a ``ui.number`` value (float | None) to a valid 0-23 hour."""
    if not isinstance(value, (int, float)):
        return 0
    return max(0, min(23, int(value)))


def _section_caption(text: str) -> None:  # pragma: no cover
    ui.label(text).classes(
        "text-[11px] font-semibold uppercase tracking-wide text-slate-400 mt-1",
    )


def _info_item(icon: str, title: str, description: str) -> None:  # pragma: no cover
    """A read-only "how it works" row: icon · (title + description)."""
    with ui.row().classes("w-full items-start gap-3 py-1 flex-nowrap"):
        ui.icon(icon).classes("text-slate-400 text-xl shrink-0 mt-0.5")
        with ui.column().classes("flex-1 gap-0 min-w-0"):
            ui.label(title).classes("text-sm font-medium text-slate-800 leading-tight")
            ui.label(description).classes("text-xs text-slate-500 leading-snug")


def _render_how_it_works() -> None:  # pragma: no cover
    """Explain the always-on engine mechanics so buyers see the full value."""
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("auto_mode").classes("text-slate-500")
            ui.label("Как работает прогрев").classes("text-base font-semibold")
        ui.label(
            "Эти механики включены всегда и работают для каждого аккаунта "
            "автоматически — настраивать их не нужно.",
        ).classes("text-xs text-slate-500")
        ui.separator()
        with ui.row().classes("w-full gap-6 items-start flex-wrap"):
            with ui.column().classes("flex-1 min-w-[300px] gap-1"):
                _section_caption("В каждом цикле")
                _info_item(
                    "visibility",
                    "Читает каналы",
                    "Заходит в каналы из списка и просматривает свежие посты — основа прогрева.",
                )
                _info_item(
                    "wifi",
                    "Онлайн и офлайн",
                    "Появляется в сети на время активности и выходит после, как живой человек.",
                )
                _info_item(
                    "schedule",
                    "Паузы как у человека",
                    "Случайные задержки между действиями: «печатает», «читает», не спешит.",
                )
                _info_item(
                    "hourglass_empty",
                    "Сон 12–30 часов",
                    "После цикла аккаунт отдыхает случайное время и повторяет активность позже.",
                )
            with ui.column().classes("flex-1 min-w-[300px] gap-1"):
                _section_caption("Защита и надёжность")
                _info_item(
                    "shield",
                    "Пауза при лимите Telegram",
                    "При flood-wait аккаунт ждёт безопасное время, а не продолжает действия.",
                )
                _info_item(
                    "restart_alt",
                    "Переживает перезапуск",
                    "После перезапуска приложения прогрев продолжается по расписанию, а не с нуля.",
                )
                _info_item(
                    "vpn_lock",
                    "Запоминает прокси старта",
                    "Фиксирует, с каким прокси аккаунт начал прогрев — удобно для разбора проблем.",
                )
                _info_item(
                    "receipt_long",
                    "Счётчики и журнал",
                    "Считает действия за день и пишет события (старт, цикл, ошибка) в живой лог.",
                )


def _feature_row(
    icon: str,
    label: str,
    description: str,
    build_control: Callable[[], ui.element],
) -> ui.element:  # pragma: no cover
    """A tidy settings row: icon · (label + description) · right-aligned control.

    Returns the control element so the caller can read its value later.
    """
    with ui.row().classes("w-full items-center gap-3 py-1 flex-nowrap"):
        ui.icon(icon).classes("text-slate-400 text-2xl shrink-0")
        with ui.column().classes("flex-1 gap-0 min-w-0"):
            ui.label(label).classes("text-sm font-medium text-slate-800 leading-tight")
            ui.label(description).classes("text-xs text-slate-500 leading-snug")
        return build_control()


async def _render_config_cards() -> None:  # pragma: no cover
    board = await load_board()
    current = board.settings
    refs: dict[str, Any] = {}

    async def persist(*, key: str | None, model: str | None, clear: bool) -> WarmingSettings:
        # Always send the full settings payload so saving one card never clobbers
        # the other: the controls below pass key/model=None (preserve), while the
        # API card passes the live control values read from ``refs``.
        return await save_settings(
            WarmingSettingsUpdate(
                inter_account_chat=bool(refs["chat"].value),
                reactions_enabled=bool(refs["reactions"].value),
                join_enabled=bool(refs["join"].value),
                enforce_readiness=bool(refs["readiness"].value),
                quiet_hours_enabled=bool(refs["quiet"].value),
                quiet_hours_start=_clamp_hour(refs["quiet_start"].value),
                quiet_hours_end=_clamp_hour(refs["quiet_end"].value),
                max_daily_actions=max(0, int(refs["daily"].value or 0)),
                gemini_api_key=key,
                gemini_model=model,
                clear_gemini_key=clear,
            ),
        )

    async def on_toggle() -> None:
        await persist(key=None, model=None, clear=False)
        ui.notify("Функции обновлены", type="positive")

    def trigger(_e: object = None) -> asyncio.Task[None]:
        return asyncio.create_task(on_toggle())

    def _switch(*, value: bool) -> ui.element:
        return ui.switch(value=value, on_change=trigger).props("dense")

    with ui.card().classes("w-[460px] p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("tune").classes("text-slate-500")
            ui.label("Функции прогрева").classes("text-base font-semibold")
        ui.label("Что делают аккаунты и какие лимиты соблюдают. Сохраняется сразу.").classes(
            "text-xs text-slate-500",
        )

        ui.separator()
        _section_caption("Поведение")
        refs["chat"] = _feature_row(
            "forum",
            "Переписка между аккаунтами",
            "ИИ-сообщения между вашими аккаунтами (нужен ключ Gemini).",
            lambda: _switch(value=current.inter_account_chat),
        )
        refs["reactions"] = _feature_row(
            "favorite",
            "Реакции на посты",
            "Иногда ставить реакции на свежие посты в каналах.",
            lambda: _switch(value=current.reactions_enabled),
        )
        refs["join"] = _feature_row(
            "add_circle",
            "Вступать в новые каналы",
            "Вступать в каналы из вашего списка — самое рискованное действие.",
            lambda: _switch(value=current.join_enabled),
        )

        ui.separator()
        _section_caption("Лимиты и безопасность")
        refs["readiness"] = _feature_row(
            "verified_user",
            "Проверка перед стартом",
            "Не запускать аккаунт без рабочего прокси, сессии и каналов.",
            lambda: _switch(value=current.enforce_readiness),
        )
        refs["quiet"] = _feature_row(
            "bedtime",
            "Тихие часы (UTC)",
            "Ночью аккаунты ничего не делают — выглядит естественнее.",
            lambda: _switch(value=current.quiet_hours_enabled),
        )
        quiet_times = ui.row().classes("w-full items-center gap-2 pl-9")
        with quiet_times:
            ui.label("с").classes("text-xs text-slate-500")
            refs["quiet_start"] = (
                ui.number(value=current.quiet_hours_start, min=0, max=23, format="%d")
                .props("dense outlined debounce=600")
                .classes("w-20")
                .on_value_change(trigger)
            )
            ui.label("до").classes("text-xs text-slate-500")
            refs["quiet_end"] = (
                ui.number(value=current.quiet_hours_end, min=0, max=23, format="%d")
                .props("dense outlined debounce=600")
                .classes("w-20")
                .on_value_change(trigger)
            )
            ui.label("часов").classes("text-xs text-slate-400")
        quiet_times.bind_visibility_from(refs["quiet"], "value")

        refs["daily"] = _feature_row(
            "speed",
            "Дневной лимит действий",
            "Максимум действий в сутки на аккаунт. 0 — без лимита.",
            lambda: (
                ui.number(value=current.max_daily_actions, min=0, format="%d")
                .props("dense outlined debounce=600")
                .classes("w-24")
                .on_value_change(trigger)
            ),
        )

    placeholder = "ключ задан" if current.has_gemini_key else "вставьте ключ для ИИ-чата"
    with ui.card().classes("w-[420px] p-4 gap-3"):
        ui.label("Настройки").classes("text-base font-semibold")
        key_input = (
            ui.input(label="API-ключ Gemini", placeholder=placeholder, password=True)
            .props("dense outlined clearable")
            .classes("w-full")
        )
        clear_key_checkbox = ui.checkbox("Удалить сохранённый ключ", value=False)
        model_input = (
            ui.input(label="Модель Gemini", value=current.gemini_model)
            .props("dense outlined")
            .classes("w-full")
        )
        status = ui.label(
            _settings_status(current.gemini_model, has_key=current.has_gemini_key),
        ).classes("text-xs text-slate-500")

        async def on_save() -> None:
            raw_key = (key_input.value or "").strip()
            raw_model = (model_input.value or "").strip()
            updated = await persist(
                key=raw_key or None,
                model=raw_model or None,
                clear=bool(clear_key_checkbox.value),
            )
            key_input.value = ""
            clear_key_checkbox.value = False
            model_input.value = updated.gemini_model
            status.set_text(_settings_status(updated.gemini_model, has_key=updated.has_gemini_key))
            ui.notify("Настройки сохранены", type="positive")

        ui.button("Сохранить настройки", icon="save", on_click=on_save).props(
            "color=primary",
        ).classes("w-full")


_CHANNEL_DELETE_SLOT = """
<q-td :props="props" class="text-right">
    <q-btn flat dense round icon="delete" color="grey-7"
           @click="() => $parent.$emit('delete', props.row)" />
</q-td>
"""


def _fmt_date(iso: str) -> str:  # pragma: no cover
    """Render an ISO timestamp as ``YYYY-MM-DD HH:MM`` for the table cell."""
    return iso[:16].replace("T", " ") if len(iso) >= 16 else iso  # noqa: PLR2004


async def _render_channels_card() -> None:  # pragma: no cover
    with ui.card().classes("w-[420px] p-4 gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Каналы").classes("text-base font-semibold")
            count_badge = ui.label("0").classes(
                "text-xs px-2 py-1 rounded bg-slate-100 text-slate-700",
            )
        channels_input = (
            ui.textarea(
                label="Добавить каналы",
                placeholder="@channel, https://t.me/channel — по одному в строке или через запятую",
            )
            .props("dense outlined autogrow")
            .classes("w-full")
        )
        columns = [
            {"name": "channel", "label": "Канал", "field": "channel", "align": "left"},
            {"name": "added", "label": "Добавлен", "field": "added", "align": "left"},
            {"name": "actions", "label": "", "field": "actions", "align": "right"},
        ]
        # Quasar diffs rows by ``row_key`` and updates in place — no flicker on
        # add/delete, unlike clearing and rebuilding a column of divs.
        table = (
            ui.table(columns=columns, rows=[], row_key="channel", pagination=0)
            .props("flat dense hide-bottom")
            .classes("w-full")
            .style("max-height: 16rem")
        )
        table.add_slot("body-cell-actions", _CHANNEL_DELETE_SLOT)

        async def refresh_list() -> None:
            board = await load_board()
            count_badge.set_text(str(board.channel_count))
            table.rows = [
                {"channel": channel.channel, "added": _fmt_date(channel.created_at)}
                for channel in board.channels.channels
            ]
            table.update()

        async def on_delete(event) -> None:  # noqa: ANN001
            await remove_channel(RemoveChannelRequest(channel=event.args["channel"]))
            await refresh_list()

        table.on("delete", on_delete)

        async def on_add() -> None:
            raw = (channels_input.value or "").strip()
            if not raw:
                ui.notify("Введите хотя бы один канал", type="warning")
                return
            result = await add_channels(AddChannelsRequest(raw=raw))
            channels_input.value = ""
            await refresh_list()
            ui.notify(f"Всего каналов: {len(result.channels)}", type="positive")

        ui.button("Добавить каналы", icon="add", on_click=on_add).props("color=primary").classes(
            "w-full",
        )
        await refresh_list()


def _board_signature(board: WarmingBoardState) -> tuple[object, ...]:  # pragma: no cover
    """A hashable digest of everything the board renders.

    The poll loop compares this between ticks and only rebuilds the DOM when it
    changes, so a quiet board never blinks.
    """
    cards = (*board.idle, *board.warming)
    return (
        board.channel_count,
        board.active_count,
        tuple(
            (
                card.account_id,
                card.state,
                card.health,
                card.cycles_completed,
                card.last_event,
                card.next_run_at,
                card.last_error,
                None
                if card.readiness is None
                else (card.readiness.ready, tuple(card.readiness.reasons)),
            )
            for card in cards
        ),
    )


def _render_board(board: WarmingBoardState, drag: dict[str, str | None], refresh) -> None:  # noqa: ANN001 # pragma: no cover
    with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
        _render_column(
            "Простой",
            "idle",
            board.idle,
            "border-slate-300",
            drag,
            refresh,
        )
        _render_column(
            f"Прогрев · активно: {board.active_count}",
            "warming",
            board.warming,
            "border-green-400",
            drag,
            refresh,
        )


def _render_column(  # noqa: PLR0913 # pragma: no cover
    title: str,
    key: str,
    cards: list[WarmingAccountState],
    border: str,
    drag: dict[str, str | None],
    refresh,  # noqa: ANN001
) -> None:
    column = ui.column().classes(
        f"tb-dropzone flex-1 min-w-[320px] p-3 gap-2 rounded-lg border-2 border-dashed "
        f"{border} bg-white min-h-[240px]",
    )

    async def on_drop() -> None:
        account_id = drag["account_id"]
        drag["account_id"] = None
        if not account_id:
            return
        if key == "warming":
            try:
                await start_warming(StartWarmingRequest(account_id=account_id))
            except WarmingNotReadyError as exc:
                reasons = "; ".join(_ru_reason(reason) for reason in exc.reasons)
                ui.notify(f"Нельзя запустить: {reasons}", type="negative")
        else:
            await stop_warming(StopWarmingRequest(account_id=account_id))
        refresh()

    column.on("dragover.prevent", lambda: None)
    column.on("drop", on_drop)
    with column:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(title).classes("text-sm font-semibold text-slate-700")
            ui.label(str(len(cards))).classes(
                "text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600",
            )
        if not cards:
            ui.label("Перетащите аккаунты сюда").classes("text-xs text-slate-400 italic")
        for card in cards:
            _render_card(card, drag)


def _render_card(  # pragma: no cover
    card: WarmingAccountState,
    drag: dict[str, str | None],
) -> None:
    pulse = " tb-active" if card.state == "active" else ""
    element = (
        ui.card()
        .props("draggable")
        .classes(
            f"w-full p-3 gap-1 cursor-grab bg-white border border-slate-200 rounded-md{pulse}",
        )
    )
    element.on("dragstart", lambda aid=card.account_id: drag.update(account_id=aid))
    with element:
        with ui.row().classes("w-full items-center gap-2"):
            ui.element("div").classes(
                f"w-2.5 h-2.5 rounded-full {_HEALTH_DOT.get(card.health, 'bg-slate-400')}",
            )
            ui.label(card.label).classes("text-sm font-medium truncate flex-1")
            ui.label(_STATE_LABEL.get(card.state, card.state)).classes(
                f"text-[11px] px-2 py-0.5 rounded {_STATE_BADGE.get(card.state, '')}",
            )
        meta = f"циклов {card.cycles_completed}"
        if card.last_event:
            meta = f"{meta} · {card.last_event}"
        ui.label(meta).classes("text-[11px] text-slate-500 truncate")
        if card.readiness and not card.readiness.ready:
            reasons = ", ".join(_ru_reason(reason) for reason in card.readiness.reasons)
            ui.label(f"не готов: {reasons}").classes("text-[11px] text-red-600 truncate")


def _render_log_row(entry: LogEntry) -> None:  # pragma: no cover
    border = _LOG_ROW_BORDER.get(entry.status, "border-slate-300")
    with ui.row().classes(f"w-full items-center gap-2 pl-2 border-l-4 {border}"):
        ui.label(entry.created_at[11:19]).classes("text-[11px] text-slate-400 w-16 shrink-0")
        ui.label(entry.account_id or "—").classes(
            "text-[11px] text-slate-500 w-28 shrink-0 truncate",
        )
        ui.label(entry.event).classes("text-xs font-medium truncate")
        if entry.extra:
            ui.label(json.dumps(entry.extra, ensure_ascii=False)).classes(
                "text-[11px] text-slate-400 truncate",
            )


async def _render_activity_log() -> None:  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("bolt").classes("text-amber-500")
            ui.label("Живая активность").classes("text-base font-semibold")
            ui.space()
            warming_only_switch = ui.switch("Только прогрев", value=True).props("dense")
        log_box = ui.column().classes("w-full gap-1 max-h-80 overflow-auto")
        seen: dict[str, object] = {"sig": None}

        async def refresh_log() -> None:
            # Pull more than _LOG_LIMIT when filtering so the warming-only view
            # is not empty after a burst of unrelated events.
            limit = _LOG_LIMIT * 4 if warming_only_switch.value else _LOG_LIMIT
            state = await load_logs_page(LogFilter(limit=limit))
            entries = state.entries
            if warming_only_switch.value:
                entries = [entry for entry in entries if entry.event.startswith("warming_")]
            entries = entries[:_LOG_LIMIT]
            # Only rebuild when the visible set of entries changed — otherwise the
            # feed re-mounts every poll and visibly blinks.
            signature = (warming_only_switch.value, tuple(entry.id for entry in entries))
            if signature == seen["sig"]:
                return
            seen["sig"] = signature
            log_box.clear()
            with log_box:
                if not entries:
                    ui.label("Ожидание активности…").classes("text-xs text-slate-400")
                for entry in entries:
                    _render_log_row(entry)

        warming_only_switch.on_value_change(lambda _e: asyncio.create_task(refresh_log()))
        await refresh_log()
        ui.timer(_LOG_POLL_SECONDS, refresh_log)
