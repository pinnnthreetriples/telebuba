"""Neurocomment page renderer — setup section + work view (issue #119).

UI-thin (non-negotiable #1), fully ``pragma: no cover``. Pure-display label maps
(``_CHANNEL_STATUS_RU`` / ``_HEALTH_RU``) are the only testable surface and are
covered in ``tests/features/test_neurocomment_labels.py``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from nicegui import ui

from features.shared import TOP_BAR_CLASSES, render_nav
from services.neurocomment import list_campaigns

if TYPE_CHECKING:
    from schemas.challenge import ChallengeRow
    from schemas.neurocomment import (
        CampaignList,
        NeurocommentBoard,
        NeurocommentRuntimeStatus,
    )

# Pure-display maps (the page's only unit-tested logic).
_CHANNEL_STATUS_RU: dict[str, str] = {
    "ready": "Готов",
    "comments_off": "Комментарии выключены",
    "join_by_request": "Вступление по заявке",
    "chat_restricted": "Блок записи (Telegram)",
    "bot_challenge": "Капча бота",
    "bot_challenge_backoff": "Капча бота · пауза",
    "throttled": "Лимит исчерпан",
}
# Each status renders a distinct badge icon (Ф2 #120). ``help_outline`` is the
# fallback for an unmapped status.
_CHANNEL_STATUS_ICON: dict[str, str] = {
    "ready": "check_circle",
    "comments_off": "comments_disabled",
    "join_by_request": "pending",
    "chat_restricted": "block",
    "bot_challenge": "smart_toy",
    "bot_challenge_backoff": "hourglass_empty",
    "throttled": "speed",
}
_HEALTH_RU: dict[str, str] = {"ready": "Готов", "blocked": "Заблокирован"}
_CAMPAIGN_STATUS_RU: dict[str, str] = {
    "active": "Активна",
    "paused": "На паузе",
    "archived": "В архиве",
}


def channel_status_label(status: str) -> str:
    """Russian label for a channel-row status (fallback: raw status)."""
    return _CHANNEL_STATUS_RU.get(status, status)


def channel_status_icon(status: str) -> str:
    """Badge icon for a channel-row status (fallback: a generic help icon)."""
    return _CHANNEL_STATUS_ICON.get(status, "help_outline")


def challenge_summary(row: ChallengeRow) -> str:
    """Drill-down summary of a failed challenge: raw text + buttons + Gemini reasoning."""
    buttons = " · ".join(row.button_labels) if row.button_labels else "—"
    text = row.raw_text.strip() or "(без текста)"
    base = f"{text} — [{buttons}]"
    if row.reasoning:
        base += f" · {row.reasoning}"
    return base


def counter_window_since(window: str, now: datetime) -> str:
    """ISO lower bound for the header-counter window; ``""`` (all rows) for 'all'."""
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if window == "7d":
        return (now - timedelta(days=7)).isoformat()
    return ""


def solver_switch_key(value: bool | None) -> str:  # noqa: FBT001 - tri-state value, not a flag
    """Map a campaign's ``solver_enabled`` to the per-campaign switch key."""
    if value is None:
        return "follow"
    return "on" if value else "off"


def health_label(health: str) -> str:
    """Russian label for an account-card health (fallback: raw value)."""
    return _HEALTH_RU.get(health, health)


def campaign_status_label(status: str) -> str:
    """Russian label for a campaign status (fallback: raw status)."""
    return _CAMPAIGN_STATUS_RU.get(status, status)


def campaign_options(campaigns: CampaignList) -> dict[str, str]:
    """Switcher options: campaign id → ``name · <status>`` label."""
    return {
        c.campaign_id: f"{c.name} · {campaign_status_label(c.status)}" for c in campaigns.campaigns
    }


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineStep:
    """One step of the on-post pipeline shown on the rail + in «Как работает».

    Mirrors the real engine flow (``services.neurocomment.engine._handle_new_post``):
    listen → new post → select account → generate → publish → monitor. ``icon`` is
    a Material glyph; ``label`` the short rail caption; ``detail`` the plain-Russian
    one-liner for the explainer card.
    """

    name: str
    label: str
    icon: str
    detail: str


# The six educational steps. The rail animates the whole sequence while the engine
# runs (it is not a per-post state machine — the board carries no per-post stage),
# so the steps are static and ordered to match the engine pipeline.
PIPELINE_STEPS: tuple[PipelineStep, ...] = (
    PipelineStep(
        "listen",
        "Слушаю",
        "hearing",
        "Аккаунт-слушатель следит за новыми постами в каналах активных кампаний.",
    ),
    PipelineStep(
        "post",
        "Новый пост",
        "post_add",
        "Свежий пост в отслеживаемом канале запускает один проход движка.",
    ),
    PipelineStep(
        "select",
        "Аккаунт",
        "person_search",
        "Выбираем готовый аккаунт: лимиты, trust-score и отсутствие кулдауна.",
    ),
    PipelineStep(
        "generate",
        "Генерация",
        "auto_awesome",
        "Gemini пишет короткий комментарий по промпту кампании, с проверкой на дубли.",
    ),
    PipelineStep(
        "publish",
        "Публикация",
        "send",
        "Комментарий уходит в обсуждение канала после паузы, как у живого человека.",
    ),
    PipelineStep(
        "monitor",
        "Контроль",
        "verified_user",
        "Следим за удалениями и капчей бота — при риске канал ставится на паузу.",
    ),
)


class FleetActivity(NamedTuple):
    """Fleet-wide live counters for the engine panel, summed from the board (pure)."""

    comments_last_hour: int
    comments_today: int
    ready_accounts: int
    total_accounts: int
    ready_channels: int
    total_channels: int


def fleet_activity(board: NeurocommentBoard) -> FleetActivity:
    """Aggregate the per-account / per-channel board figures into fleet totals."""
    return FleetActivity(
        comments_last_hour=sum(c.comments_last_hour for c in board.accounts),
        comments_today=sum(c.comments_today for c in board.accounts),
        ready_accounts=sum(1 for c in board.accounts if c.health == "ready"),
        total_accounts=len(board.accounts),
        ready_channels=sum(1 for r in board.channels if r.status == "ready"),
        total_channels=len(board.channels),
    )


def relative_time(iso: str | None, now: datetime) -> str | None:
    """Human «X назад» for a past ISO timestamp; ``None`` when missing/unparseable."""
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    delta = (now - stamp).total_seconds()
    if delta < 60:  # noqa: PLR2004 - the minute boundary reads clearer inline than as a constant
        return "только что"
    if delta < 3600:  # noqa: PLR2004 - hour in seconds
        return f"{int(delta // 60)} мин назад"
    if delta < 86_400:  # noqa: PLR2004 - day in seconds
        return f"{int(delta // 3600)} ч назад"
    return f"{int(delta // 86_400)} д назад"


def runtime_status_text(status: NeurocommentRuntimeStatus) -> str:
    """Short pill label for the engine's running state."""
    if not status.running:
        return "Движок остановлен"
    if status.active_channels == 0:
        return "Движок запущен"
    return f"Слушаю каналов: {status.active_channels}"


def _build_header() -> None:  # pragma: no cover
    with ui.row().classes(TOP_BAR_CLASSES):
        render_nav("/neurocomment")


async def render_neurocomment_page() -> None:  # pragma: no cover
    # Lazy imports: the sibling render modules import this module's pure helpers at
    # module level, so importing them here (not at top) avoids an import cycle.
    from features.neurocomment._explainer import render_how_it_works  # noqa: PLC0415
    from features.neurocomment._setup import render_create_campaign  # noqa: PLC0415

    ui.query("body").classes("bg-slate-50 text-slate-950")
    _build_header()
    with ui.column().classes("w-full max-w-[1200px] mx-auto p-4 gap-4"):
        ui.label("Нейрокомментинг").classes("text-xl font-semibold")
        campaign_list = await list_campaigns()
        # No campaign yet → just the create form (open) + the explainer.
        await render_create_campaign(on_created=_reload_page, expanded=not campaign_list.campaigns)
        if not campaign_list.campaigns:
            render_how_it_works()
            return

        # Switching a campaign re-renders the whole section (engine panel → setup →
        # work view). Each panel owns its own poll timer; the refresh clears the old
        # elements (dropping their timers) before mounting the new campaign's.
        @ui.refreshable
        async def section() -> None:
            from features.neurocomment._engine_panel import render_engine_panel  # noqa: PLC0415
            from features.neurocomment._setup import render_setup  # noqa: PLC0415
            from features.neurocomment._workview import render_work_view  # noqa: PLC0415

            await render_engine_panel(switcher.value)
            await render_setup(switcher.value)
            await render_work_view(switcher.value)

        def on_switch() -> None:
            # Named (not ``on_change=section.refresh``) so the select's change-event
            # arg isn't forwarded into the zero-arg ``section()``.
            section.refresh()

        switcher = (
            ui.select(
                campaign_options(campaign_list),
                label="Кампания",
                value=campaign_list.campaigns[-1].campaign_id,
                on_change=on_switch,
            )
            .props("dense outlined")
            .classes("w-full max-w-[400px]")
        )
        await section()
        render_how_it_works()


def _reload_page() -> None:  # pragma: no cover
    ui.navigate.to("/neurocomment")
