"""Dialogues overview + live activity log rendering.

UI-thin per non-negotiable #1; excluded from coverage. Logic lives in
``services.dialogues`` / ``services.logs``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from nicegui import context, ui

from features.warming._board import _BOARD_POLL_SECONDS
from schemas.logs import LogFilter
from services.dialogues import load_dialogue_overview
from services.logs import load_logs_page

if TYPE_CHECKING:
    from schemas.dialogues import DialogueOverview
    from schemas.logs import LogEntry

_LOG_POLL_SECONDS = 2.0
_LOG_LIMIT = 40

_LOG_ROW_BORDER = {
    "success": "border-green-500",
    "warning": "border-amber-500",
    "error": "border-red-500",
}


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


def _render_dialogue_body(overview: DialogueOverview) -> None:  # pragma: no cover
    if not overview.pairs:
        ui.label("Пар пока нет — появятся, когда прогреваются 2+ аккаунта.").classes(
            "text-xs text-slate-400",
        )
    else:
        with ui.row().classes("w-full gap-1 flex-wrap"):
            for pair in overview.pairs:
                ui.label(f"{pair.account_a} ↔ {pair.account_b}").classes(
                    "text-[11px] px-2 py-0.5 rounded bg-indigo-50 text-indigo-700",
                )
    if overview.recent:
        ui.separator()
        for message in overview.recent:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(message.created_at[11:19]).classes(
                    "text-[11px] text-slate-400 w-16 shrink-0",
                )
                ui.label(f"{message.from_account} → {message.to_account}").classes(
                    "text-[11px] text-slate-500 w-40 shrink-0 truncate",
                )
                ui.label(message.text).classes("text-xs truncate")


async def _render_dialogues() -> None:  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("forum").classes("text-indigo-500")
            ui.label("Диалоги между аккаунтами").classes("text-base font-semibold")
        ui.label(
            "Аккаунты переписываются друг с другом по парам — ход за ходом, с паузами; "
            "беседа естественно затухает и может возобновиться позже.",
        ).classes("text-xs text-slate-500")
        body = ui.column().classes("w-full gap-2")
        seen: dict[str, object] = {"sig": None}

        async def refresh_dialogues() -> None:
            overview = await load_dialogue_overview(recent_limit=12)
            signature = (
                tuple((pair.account_a, pair.account_b) for pair in overview.pairs),
                tuple(message.id for message in overview.recent),
            )
            if signature == seen["sig"]:
                return
            seen["sig"] = signature
            body.clear()
            with body:
                _render_dialogue_body(overview)

        await refresh_dialogues()
        t = ui.timer(_BOARD_POLL_SECONDS, refresh_dialogues)
        context.client.on_disconnect(t.cancel)


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
                entries = [
                    entry
                    for entry in entries
                    if entry.event.startswith("warming_")
                    or entry.event.startswith("telegram_")
                    or entry.event.startswith("dialogue_")
                ]
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
        t = ui.timer(_LOG_POLL_SECONDS, refresh_log)
        context.client.on_disconnect(t.cancel)
