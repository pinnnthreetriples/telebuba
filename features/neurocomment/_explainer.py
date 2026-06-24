"""Neurocomment «Как работает» explainer card (page redesign).

A static, read-only card that teaches the operator what the engine does on every
post (the same six steps the rail animates) plus the anti-ban guards. Mirrors the
shape of ``features/warming/_config._render_how_it_works`` without importing it
(no cross-feature imports — non-negotiable #1). UI-thin, ``# pragma: no cover``.
"""

from __future__ import annotations

from nicegui import ui

from core.config import settings
from features.neurocomment._page import PIPELINE_STEPS


def _section_caption(text: str) -> None:  # pragma: no cover
    ui.label(text).classes(
        "text-[11px] font-semibold uppercase tracking-wide text-slate-400 mt-1",
    )


def _info_item(icon: str, title: str, description: str) -> None:  # pragma: no cover
    with ui.row().classes("w-full items-start gap-3 py-1 flex-nowrap"):
        ui.icon(icon).classes("text-slate-400 text-xl shrink-0 mt-0.5")
        with ui.column().classes("flex-1 gap-0 min-w-0"):
            ui.label(title).classes("text-sm font-medium text-slate-800 leading-tight")
            ui.label(description).classes("text-xs text-slate-500 leading-snug")


def render_how_it_works() -> None:  # pragma: no cover
    """Two-column explainer: the on-post pipeline (left) + protection & limits (right)."""
    nc = settings.neurocomment
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("auto_mode").classes("text-slate-500")
            ui.label("Как работает нейрокомментинг").classes("text-base font-semibold")
        ui.label(
            "Движок слушает новые посты в каналах кампании и оставляет короткий "
            "ИИ-комментарий от готового аккаунта. Вот что происходит на каждом посте:",
        ).classes("text-xs text-slate-500")
        ui.separator()
        with ui.row().classes("w-full gap-6 items-start flex-wrap"):
            with ui.column().classes("flex-1 min-w-[300px] gap-1"):
                _section_caption("Что происходит при запуске")
                for step in PIPELINE_STEPS:
                    _info_item(step.icon, step.label, step.detail)
            with ui.column().classes("flex-1 min-w-[300px] gap-1"):
                _section_caption("Защита и лимиты")
                _info_item(
                    "schedule",
                    "Паузы как у человека",
                    "Случайная задержка перед каждым комментарием — не мгновенный ответ бота.",
                )
                _info_item(
                    "speed",
                    "Лимиты на аккаунт",
                    f"Не больше {nc.max_comments_per_hour} комментариев в час на аккаунт; "
                    "суточный лимит на канал — в конфиге.",
                )
                _info_item(
                    "content_copy",
                    "Без повторов",
                    "Дубли и слишком похожие комментарии отбраковываются до отправки.",
                )
                _info_item(
                    "shield",
                    "Пауза при риске",
                    "Массовое удаление комментариев или капча бота ставят канал на паузу.",
                )
                _info_item(
                    "restart_alt",
                    "Переживает перезапуск",
                    "После рестарта приложения слушатель восстанавливается сам.",
                )
