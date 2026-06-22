"""Dialogues overview + live activity log rendering.

UI-thin per non-negotiable #1; excluded from coverage. Logic lives in
``services.dialogues`` / ``services.logs``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import context, ui

from features.warming._board_styling import _BOARD_POLL_SECONDS
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
        # See features/warming/__init__.py for why the lambda wrapper is necessary.
        dialogues_timer = ui.timer(_BOARD_POLL_SECONDS, refresh_dialogues)
        context.client.on_disconnect(lambda: dialogues_timer.cancel(with_current_invocation=True))


async def _render_activity_log() -> None:  # pragma: no cover
    # Global problems feed: every account's warnings + errors in one place, so an
    # operator sees a failure anywhere without expanding individual cards. The
    # success-level "what happened" stream now lives per-card («Логи аккаунта»).
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("report").classes("text-red-500")
            ui.label("Ошибки и предупреждения").classes("text-base font-semibold")
            ui.label("по всем аккаунтам").classes("text-xs text-slate-400")
        log_box = ui.column().classes("w-full gap-1 max-h-80 overflow-auto")
        seen: dict[str, object] = {"sig": None}

        async def refresh_log() -> None:
            state = await load_logs_page(LogFilter(problems_only=True, limit=_LOG_LIMIT))
            entries = state.entries
            # Only rebuild when the visible set of entries changed — otherwise the
            # feed re-mounts every poll and visibly blinks.
            signature = tuple(entry.id for entry in entries)
            if signature == seen["sig"]:
                return
            seen["sig"] = signature
            log_box.clear()
            with log_box:
                if not entries:
                    ui.label("Ошибок нет — все аккаунты работают штатно.").classes(
                        "text-xs text-slate-400",
                    )
                for entry in entries:
                    _render_log_row(entry)

        await refresh_log()
        # See features/warming/__init__.py for why the lambda wrapper is necessary.
        log_timer = ui.timer(_LOG_POLL_SECONDS, refresh_log)
        context.client.on_disconnect(lambda: log_timer.cancel(with_current_invocation=True))
