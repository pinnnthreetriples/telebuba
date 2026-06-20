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


# Recommended quiet-hour presets — picked to match human-believable sleep
# windows in the account's local timezone. Telegram's 2026 ban system tracks
# activity vs. local time as a behavioural signal, so a plausible night
# window is meaningful protection (see plan §9 sources). Each value is the
# pair "start hour, end hour" in 0-23.
_TOGGLE_SAVE_TIMEOUT_SECONDS = 10.0

_QUIET_PRESET_OFF = "off"
_QUIET_PRESET_CUSTOM = "custom"
_QUIET_PRESETS: dict[str, tuple[int, int]] = {
    "night_23_07": (23, 7),
    "night_00_08": (0, 8),
    "long_22_09": (22, 9),
}
_QUIET_PRESET_LABELS: dict[str, str] = {
    _QUIET_PRESET_OFF: "Без тихих часов",
    "night_23_07": "🌙 Ночь 23 → 07 · стандарт",
    "night_00_08": "🌙 Ночь 00 → 08",
    "long_22_09": "😴 Длинный отдых 22 → 09",
    _QUIET_PRESET_CUSTOM: "⚙ Своё расписание",
}


def _detect_quiet_preset(*, enabled: bool, start: int, end: int) -> str:
    """Return the quiet-hours preset key matching the saved state.

    ``"off"`` when the toggle is off; the matching preset key when the
    saved (start, end) pair matches a preset exactly; ``"custom"``
    otherwise. Pure — UI uses it on load to pick the right select option.
    """
    if not enabled:
        return _QUIET_PRESET_OFF
    for key, (preset_start, preset_end) in _QUIET_PRESETS.items():
        if (start, end) == (preset_start, preset_end):
            return key
    return _QUIET_PRESET_CUSTOM


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
                _info_item(
                    "auto_awesome",
                    "Авто-лимит действий",
                    "Каждому аккаунту свой лимит на сутки — по фазе прогрева и trust score. "
                    "См. карточку аккаунта.",
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
                # Auto-cap is per-account; this fleet-wide value is preserved
                # verbatim — a legacy override persisted in the DB settings row
                # (UI no longer exposes it).
                max_daily_actions=current.max_daily_actions,
                gemini_api_key=key,
                gemini_model=model,
                clear_gemini_key=clear,
            ),
        )

    save_lock = asyncio.Lock()

    async def on_toggle() -> None:
        # Serialise saves: rapid toggles must persist in the order the
        # operator clicked them. Without the lock two ``save_settings``
        # calls would race and the row would non-deterministically reflect
        # the FIRST-finishing request, not the LAST clicked one.
        try:
            await asyncio.wait_for(
                save_lock.acquire(),
                timeout=_TOGGLE_SAVE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            ui.notify("Ошибка сохранения: таймаут", type="negative")
            return
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
            # Quiet-hour preset mirrors the underlying values — re-detect after
            # rollback so the chosen preset matches what's actually persisted.
            refs["quiet_preset"].value = _detect_quiet_preset(
                enabled=fresh.quiet_hours_enabled,
                start=fresh.quiet_hours_start,
                end=fresh.quiet_hours_end,
            )
        finally:
            save_lock.release()

    async def trigger(_e: object = None) -> None:
        # Async + awaited keeps the client's slot context alive inside
        # ``on_toggle`` so ``ui.notify`` can resolve ``context.client``.
        # An ``asyncio.create_task`` wrapper would drop the slot stack.
        await on_toggle()

    def switch(*, value: bool) -> ui.element:
        return ui.switch(value=value, on_change=trigger).props("dense")

    _render_features_card(current, refs, switch=switch, trigger=trigger)
    _render_gemini_card(current, persist=persist)


def _render_features_card(
    current: WarmingSettings,
    refs: dict[str, Any],
    *,
    switch: Callable[..., ui.element],
    trigger: Callable[..., Awaitable[None]],
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
        _render_quiet_hours_block(current, refs, trigger=trigger)


def _render_quiet_hours_block(
    current: WarmingSettings,
    refs: dict[str, Any],
    *,
    trigger: Callable[..., Awaitable[None]],
) -> None:  # pragma: no cover
    # Hidden state-holder for ``quiet_hours_enabled``. The user-facing
    # control is a preset select; this switch is the boolean ``persist()``
    # reads, kept in sync by the select's on_change.
    refs["quiet"] = ui.switch(value=current.quiet_hours_enabled).classes("hidden")

    initial_quiet_preset = _detect_quiet_preset(
        enabled=current.quiet_hours_enabled,
        start=current.quiet_hours_start,
        end=current.quiet_hours_end,
    )

    async def on_quiet_preset(e: object) -> None:
        key = getattr(e, "value", None) or refs["quiet_preset"].value
        if key == _QUIET_PRESET_OFF:
            refs["quiet"].value = False
        elif key == _QUIET_PRESET_CUSTOM:
            refs["quiet"].value = True
            # A start==end window reads as "no quiet hours" to the engine
            # (_in_quiet_hours), so a custom preset left at the 0→0 default
            # would show as enabled yet stay silent. Seed a sane night window.
            if _clamp_hour(refs["quiet_start"].value) == _clamp_hour(refs["quiet_end"].value):
                refs["quiet_start"].value = 23
                refs["quiet_end"].value = 7
        elif key in _QUIET_PRESETS:
            start_hour, end_hour = _QUIET_PRESETS[key]
            refs["quiet"].value = True
            refs["quiet_start"].value = start_hour
            refs["quiet_end"].value = end_hour
        await trigger(e)

    refs["quiet_preset"] = _feature_row(
        "bedtime",
        "Локальное время аккаунта",
        "Ночью аккаунты молчат — выглядит как сон по локали аккаунта.",
        lambda: (
            ui.select(
                _QUIET_PRESET_LABELS,
                value=initial_quiet_preset,
                on_change=on_quiet_preset,
            )
            .props("dense outlined options-dense")
            .classes("w-56")
        ),
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
    quiet_times.bind_visibility_from(
        refs["quiet_preset"],
        "value",
        value=_QUIET_PRESET_CUSTOM,
    )

    # Per-account daily cap is now auto, derived from each account's
    # warming phase + trust band — see services.warming.pacing. The
    # legacy fleet-wide ``max_daily_actions`` setting stays in the schema
    # but is no longer surfaced in the UI: ``persist()`` passes
    # ``current.max_daily_actions`` through verbatim, so a previously-set
    # override persists in the DB settings row (the .env default applies
    # only when the column is unset).


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
