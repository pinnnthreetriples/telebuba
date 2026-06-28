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
    ui.label(text).classes("tb-uplabel").style("margin-top:4px")


def _info_item(icon: str, title: str, description: str) -> None:  # pragma: no cover
    with ui.row().classes("w-full items-start gap-3 py-1 flex-nowrap"):
        ui.icon(icon).style("color:#9A9893;font-size:20px;flex-shrink:0;margin-top:2px")
        with ui.column().classes("flex-1 gap-0 min-w-0"):
            ui.label(title).style("font-size:13px;font-weight:500;color:#3A3A3A;line-height:1.25")
            ui.label(description).style("font-size:12px;color:#5C5C5C;line-height:1.4")


def render_how_it_works() -> None:  # pragma: no cover
    """Two-column explainer: the on-post pipeline (left) + protection & limits (right)."""
    nc = settings.neurocomment
    with ui.element("div").classes("tb-card-soft w-full").style("padding:16px 18px"):
        with ui.row().classes("w-full items-center gap-2 flex-nowrap").style("margin-bottom:4px"):
            ui.icon("auto_mode").style("color:#74726E")
            ui.label("Как работает нейрокомментинг").classes("tb-title-lg")
        ui.label(
            "Движок слушает новые посты в каналах кампании и оставляет короткий "
            "ИИ-комментарий от готового аккаунта. Вот что происходит на каждом посте:",
        ).classes("tb-muted").style("font-size:12px")
        ui.html(
            '<div style="height:1px;background:#F0EEEB;width:100%;margin:8px 0"></div>',
        )
        with ui.row().classes("w-full gap-6 items-start flex-wrap"):
            with ui.column().classes("flex-1 min-w-[260px] gap-1"):
                _section_caption("Что происходит при запуске")
                for step in PIPELINE_STEPS:
                    _info_item(step.icon, step.label, step.detail)
            with ui.column().classes("flex-1 min-w-[260px] gap-1"):
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
