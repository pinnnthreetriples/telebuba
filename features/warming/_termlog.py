"""Per-account activity log — a dark "terminal" panel inside each warming card.

UI-thin per non-negotiable #1; renderers carry ``# pragma: no cover`` and the
panel reads only the polled per-account log entries the board fetches via
``services.logs`` — no DB/SDK here. The pure helpers (``_event_label`` /
``_format_extra`` / ``_toggle_expanded``) carry the only logic and are unit
tested in ``tests/features/test_termlog.py``.

The CSS is registered once via ``ui.add_css(shared=True)`` at import (same
mechanism as the pipeline keyframes) so the dark theme travels with this
renderer to every client.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nicegui import ui

from features.warming._board_checks import _ru_reason

if TYPE_CHECKING:
    from features.warming._board import _BoardContext
    from schemas.logs import LogEntry
    from schemas.warming import WarmingAccountState

# Hard cap on the rendered ``key=value`` summary so one fat ``extra`` payload
# can't blow out a log row. Mirrors ``_ERROR_DETAIL_MAX_LEN`` in ``_pipeline``.
_EXTRA_MAX_LEN = 80

# Dark terminal theme — spec C.3 terminal palette (#16161A panel, JetBrains
# Mono, #5C5C66 timestamp, blue/green/amber/red message accents). Mirrors the
# shared ``.tb-term`` vocabulary while keeping this panel's icon + key=value row.
_TERMLOG_CSS = """
.tb-termlog {
    background: #16161A;
    border-radius: 9px;
    padding: 10px 11px;
    max-height: 120px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    line-height: 1.7;
}
.tb-termlog::-webkit-scrollbar { width: 6px; }
.tb-termlog::-webkit-scrollbar-thumb { background: #2b2b2e; border-radius: 6px; }
.tb-termlog-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 1px 0;
    white-space: nowrap;
}
.tb-termlog-time { color: #5C5C66; font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.tb-termlog-msg { color: #7FB2FF; flex: 0 0 auto; }
.tb-termlog-kv { color: #C9C9CE; overflow: hidden; text-overflow: ellipsis; }
.tb-termlog-empty { color: #5C5C66; font-style: italic; }
.tb-tl-ok { color: #9FE6B8; }
.tb-tl-warn { color: #FFD27F; }
.tb-tl-err { color: #F08C84; }
"""

ui.add_css(_TERMLOG_CSS, shared=True)

# Status → accent class for the row icon (and the label when not a plain step).
_TERMLOG_ACCENT: dict[str, str] = {
    "success": "tb-tl-ok",
    "warning": "tb-tl-warn",
    "error": "tb-tl-err",
}

# event name → (Material icon, Russian label). Keyed by the real events the
# engine + telegram gateway emit (verified in core/telegram_client/_actions.py
# and services/warming/**). Unknown events humanise via _event_label's fallback.
_EVENT_LABEL: dict[str, tuple[str, str]] = {
    # Per-step success — the live rail mirrored into history.
    "telegram_set_online": ("wifi", "Онлайн"),
    "telegram_join_channel": ("add_circle", "Вступил в канал"),
    "telegram_join_channel_already_participant": ("check_circle", "Уже в канале"),
    "telegram_read_channel": ("chrome_reader_mode", "Прочитал канал"),
    "telegram_react_to_post": ("thumb_up", "Поставил реакцию"),
    "telegram_send_dm": ("forum", "Отправил сообщение"),
    "telegram_update_profile": ("badge", "Обновил профиль"),
    "telegram_set_profile_photo": ("photo_camera", "Сменил фото"),
    "telegram_post_story": ("auto_stories", "Опубликовал историю"),
    "telegram_remove_story": ("delete", "Удалил историю"),
    "telegram_add_profile_music": ("music_note", "Добавил музыку"),
    "telegram_remove_profile_music": ("music_off", "Убрал музыку"),
    # Per-step failures — the common warming ones.
    "telegram_set_online_failed": ("wifi_off", "Онлайн не удался"),
    "telegram_join_channel_failed": ("error", "Не вступил в канал"),
    "telegram_read_channel_failed": ("error", "Не прочитал канал"),
    "telegram_react_to_post_failed": ("error", "Реакция не удалась"),
    "telegram_send_dm_failed": ("error", "Сообщение не ушло"),
    # Connection.
    "telegram_pool_connect_failed": ("link_off", "Не подключился"),
    "telegram_pool_connect_retry": ("autorenew", "Повтор подключения"),
    # Warming lifecycle / cycle / phase.
    "warming_started": ("play_arrow", "Прогрев запущен"),
    "warming_stopped": ("stop", "Прогрев остановлен"),
    "warming_cycle_completed": ("done_all", "Цикл завершён"),
    "phase_advanced": ("trending_up", "Новая фаза"),
    "warming_no_channels": ("block", "Нет каналов"),
    "warming_start_blocked": ("block", "Старт заблокирован"),
    "warming_set_offline_failed": ("wifi_off", "Офлайн не удался"),
    "warming_progress_write_failed": ("warning", "Сбой записи прогресса"),
    "warming_loop_crashed": ("error", "Сбой цикла"),
    "warming_reconcile_not_ready": ("block", "Не готов к запуску"),
    "warming_cycle_not_ready": ("block", "Не готов к циклу"),
    "warming_chat_generation_failed": ("error", "Не сгенерировал сообщение"),
    "warming_stop_task_error": ("warning", "Ошибка остановки задачи"),
    "warming_dialogue_pair_refresh_failed": ("error", "Сбой пересборки пар"),
    "warming_shutdown_timeout": ("warning", "Задачи не завершились вовремя"),
    "warming_channel_limit_reached": ("block", "Достигнут лимит каналов"),
    # Quarantine.
    "warming_quarantine_recovered": ("lock_open", "Карантин снят"),
    "warming_quarantine_extended": ("lock_clock", "Карантин продлён"),
    "warming_quarantine_exhausted": ("gpp_bad", "Карантин исчерпан"),
    # Dialogues.
    "warming_dialogue_opened": ("forum", "Диалог начат"),
    "warming_dialogue_reply": ("reply", "Ответ в диалоге"),
    "warming_dialogue_faded": ("hourglass_bottom", "Диалог затих"),
}


def _event_label(entry: LogEntry) -> tuple[str, str]:
    """Map a log event to ``(material_icon, russian_label)``.

    Unknown events fall back to a humanised form of the raw event name so a
    newly added event still reads cleanly instead of vanishing.
    """
    known = _EVENT_LABEL.get(entry.event)
    if known is not None:
        return known
    # Telegram rate-limit family — one rule covers every action's flood variants.
    if entry.event.endswith(("_flood_wait", "_slow_mode_wait", "_premium_wait", "_peer_flood")):
        return ("timer", "Лимит Telegram")
    return ("circle", entry.event.replace("_", " "))


def _format_extra(extra: dict[str, object]) -> str:
    """Render an event's ``extra`` as a compact ``key=value`` string.

    Empty/None values are dropped (``0`` and ``False`` are kept — they carry
    meaning, e.g. ``online=False`` / ``reactions=0``); the result is capped so
    one row can't run off the panel.
    """
    parts = [f"{key}={value}" for key, value in extra.items() if value not in (None, "", [], {})]
    text = " ".join(parts)
    if len(text) > _EXTRA_MAX_LEN:
        text = text[: _EXTRA_MAX_LEN - 1] + "…"
    return text


def _toggle_expanded(state: dict[str, bool], account_id: str) -> bool:
    """Flip the expand flag for ``account_id`` and return the new value."""
    new_value = not state.get(account_id, False)
    state[account_id] = new_value
    return new_value


def _humanize_detail(event: str, extra: dict[str, object]) -> str:  # noqa: PLR0911 - flat translation dispatch reads clearer as early returns.
    """Plain-Russian one-line reason for a log row (operator-facing).

    Translates the common warming failures; unknown payloads fall back to the
    raw message, then to a compact ``key=value`` dump — so nothing reads as a
    cryptic event name + JSON.
    """
    seconds = extra.get("seconds")
    if seconds is not None:
        return f"ждём {seconds} с"
    reasons = extra.get("reasons")
    if isinstance(reasons, list) and reasons:
        return ", ".join(_ru_reason(str(reason)) for reason in reasons)
    message = str(extra.get("message") or "")
    if extra.get("error_type") == "AuthKeyDuplicatedError":
        return "сессия использовалась с двух устройств — больше не работает"
    if "No user has" in message or "Cannot find any entity" in message:
        return "канал не найден"
    if event == "warming_cycle_completed":
        return f"ошибок в цикле: {extra.get('failures') or 0}"
    channel = extra.get("channel")
    if channel is not None:
        return str(channel)
    return message or _format_extra(extra)


def _render_termlog_row(entry: LogEntry) -> None:  # pragma: no cover
    accent = _TERMLOG_ACCENT.get(entry.status, "")
    icon, label = _event_label(entry)
    # A plain successful step stays clean white; warnings/errors tint the label
    # too so they pop against the stream.
    label_cls = "tb-termlog-msg" if entry.status == "success" else f"tb-termlog-msg {accent}"
    with ui.row().classes("tb-termlog-row w-full"):
        ui.label(entry.created_at[11:19]).classes("tb-termlog-time")
        ui.icon(icon).classes(f"text-sm {accent}")
        ui.label(label).classes(label_cls)
        summary = _humanize_detail(entry.event, entry.extra)
        if summary:
            ui.label(summary).classes("tb-termlog-kv")


def render_card_log_panel(
    ctx: _BoardContext,
    card: WarmingAccountState,
) -> None:  # pragma: no cover
    """Render the expandable per-account activity panel inside a card.

    A click-to-toggle header plus a dark terminal log of that account's recent
    steps (newest-first). Expansion state + fetched entries live on ``ctx``, so
    they survive the board's 4-second poll re-render. The click delegates to
    ``ctx.on_toggle_log`` (wired in the page module) which flips the flag,
    fetches the logs, and refreshes just this card.
    """
    account_id = card.account_id
    expanded = ctx.card_expanded.get(account_id, False)
    chevron = "expand_less" if expanded else "expand_more"
    header = ui.row().classes(
        "w-full items-center gap-1 cursor-pointer select-none pt-1 "
        "border-t border-[#F0EEEB] text-[#74726E]",
    )
    with header:
        ui.label("Скрыть лог" if expanded else "Лог активности").classes("text-[11px]")
        ui.icon(chevron).classes("text-base")
    # Wrap the async toggle in create_task — a click handler that merely returns
    # a coroutine is not reliably awaited (matches the _activity.py pattern).
    header.on(
        "click",
        lambda: (
            asyncio.create_task(ctx.on_toggle_log(account_id))
            if ctx.on_toggle_log is not None
            else None
        ),
    )
    if not expanded:
        return
    entries = ctx.card_logs.get(account_id)
    with ui.element("div").classes("tb-termlog w-full"):
        if entries is None:
            ui.label("Загрузка…").classes("tb-termlog-empty")
        elif not entries:
            ui.label("Нет событий").classes("tb-termlog-empty")
        else:
            for entry in entries:
                _render_termlog_row(entry)
