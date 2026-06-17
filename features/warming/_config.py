"""Warming configuration cards — feature toggles, limits, Gemini settings, help.

UI-thin per non-negotiable #1; excluded from coverage like the other feature
rendering. Logic lives in ``services.warming``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from nicegui import ui

from schemas.warming import WarmingSettingsUpdate
from services.warming import load_board, save_settings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.warming import WarmingSettings


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
        try:
            await persist(key=None, model=None, clear=False)
            ui.notify("Функции обновлены", type="positive")
        except Exception as exc:  # noqa: BLE001
            ui.notify(f"Ошибка сохранения: {exc}", type="negative")
            # Rollback to the last known good state
            fresh_board = await load_board()
            fresh = fresh_board.settings
            refs["chat"].value = fresh.inter_account_chat
            refs["reactions"].value = fresh.reactions_enabled
            refs["join"].value = fresh.join_enabled
            refs["readiness"].value = fresh.enforce_readiness
            refs["quiet"].value = fresh.quiet_hours_enabled
            refs["quiet_start"].value = fresh.quiet_hours_start
            refs["quiet_end"].value = fresh.quiet_hours_end
            refs["daily"].value = fresh.max_daily_actions

    def trigger(_e: object = None) -> asyncio.Task[None]:
        return asyncio.create_task(on_toggle())

    def switch(*, value: bool) -> ui.element:
        return ui.switch(value=value, on_change=trigger).props("dense")

    _render_features_card(current, refs, switch=switch, trigger=trigger)
    _render_gemini_card(current, persist=persist)


def _render_features_card(
    current: WarmingSettings,
    refs: dict[str, Any],
    *,
    switch: Callable[..., ui.element],
    trigger: Callable[..., object],
) -> None:  # pragma: no cover
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
            lambda: switch(value=current.inter_account_chat),
        )
        refs["reactions"] = _feature_row(
            "favorite",
            "Реакции на посты",
            "Иногда ставить реакции на свежие посты в каналах.",
            lambda: switch(value=current.reactions_enabled),
        )
        refs["join"] = _feature_row(
            "add_circle",
            "Вступать в новые каналы",
            "Вступать в каналы из вашего списка — самое рискованное действие.",
            lambda: switch(value=current.join_enabled),
        )

        ui.separator()
        _section_caption("Лимиты и безопасность")
        refs["readiness"] = _feature_row(
            "verified_user",
            "Проверка перед стартом",
            "Не запускать аккаунт без рабочего прокси, сессии и каналов.",
            lambda: switch(value=current.enforce_readiness),
        )
        refs["quiet"] = _feature_row(
            "bedtime",
            "Локальное время аккаунта",
            "Ночью аккаунты ничего не делают — выглядит естественнее.",
            lambda: switch(value=current.quiet_hours_enabled),
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


def _render_gemini_card(
    current: WarmingSettings,
    *,
    persist: Callable[..., Awaitable[WarmingSettings]],
) -> None:  # pragma: no cover
    # Gemini credentials are .env-managed (security: not stored in the SQLite
    # backup). This card is read-only — operators rotate the key in .env and
    # restart. ``persist`` is kept on the signature so the caller-side wiring
    # does not change.
    del persist
    status_label = "Ключ задан в .env" if current.has_gemini_key else "Ключ Gemini не задан в .env"
    with ui.card().classes("w-[420px] p-4 gap-3"):
        ui.label("Gemini (управляется через .env)").classes("text-base font-semibold")
        ui.label(status_label).classes("text-sm")
        ui.label(f"Модель: {current.gemini_model}").classes("text-xs text-slate-500")
        ui.label(
            "Чтобы заменить или ротировать ключ, отредактируйте `.env` "
            "(GEMINI__API_KEY) и перезапустите приложение.",
        ).classes("text-xs text-slate-500")
