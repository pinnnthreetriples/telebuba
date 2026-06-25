"""Neurocomment page renderer — setup section + work view (issue #119).

UI-thin (non-negotiable #1), fully ``pragma: no cover``. Pure-display label maps
(``_CHANNEL_STATUS_RU`` / ``_HEALTH_RU``) are the only testable surface and are
covered in ``tests/features/test_neurocomment_labels.py``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from nicegui import context, ui

from core.logging import log_event
from features.shared import TOP_BAR_CLASSES, render_nav
from services.neurocomment import (
    list_campaigns,
    load_neurocomment_board,
    neurocomment_runtime_status,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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


def start_block_reason(ready_accounts: int, *, has_listener: bool) -> str | None:
    """Why «Запустить» must stay blocked, or ``None`` when the engine may start.

    Ties the operator-facing warning to the real gate: starting with no listener or
    with zero ready accounts is a silent no-op that erodes trust, so the button refuses
    and names what is missing — the «нет прогретых аккаунтов» message and the actual
    behaviour now agree.
    """
    if not has_listener:
        return "Выберите аккаунт-слушатель"
    if ready_accounts <= 0:
        return "Нет готовых аккаунтов — добавьте и онбордните их в «Настройке»."
    return None


def board_content_signature(board: NeurocommentBoard | None) -> tuple[object, ...]:
    """Digest of everything the work view renders; equal digests → skip the re-render.

    Anti-flicker: the work view polls every few seconds but only re-renders the board
    when this signature changes (mirrors warming's ``_card_signature`` gate), so an
    idle board no longer blinks.
    """
    if board is None:
        return ()
    return (
        board.solver_enabled,
        tuple((r.channel, r.status, r.ready_accounts, r.total_accounts) for r in board.channels),
        tuple(
            (
                c.account_id,
                c.health,
                c.comments_last_hour,
                c.max_comments_per_hour,
                c.comments_today,
                c.trust_score,
                c.trust_band,
                c.spam_status,
                c.last_comment_at,
            )
            for c in board.accounts
        ),
    )


def live_signature(
    status: NeurocommentRuntimeStatus,
    activity: FleetActivity,
    last_comment: str | None,
) -> tuple[object, ...]:
    """Digest of the engine panel's live section (status pill + ticker + counters).

    ``last_comment`` is the already-bucketed «X назад» string, so the relative clock
    only flips the digest about once a minute instead of every poll — no 4 s blink.
    """
    return (status.running, status.active_channels, *activity, last_comment)


class PageContext:
    """Client session context holding pre-loaded board and status for shared polling."""

    def __init__(self, campaign_id: str) -> None:
        self.campaign_id = campaign_id
        self.status: NeurocommentRuntimeStatus | None = None
        self.board: NeurocommentBoard | None = None
        self.on_reload_callbacks: list[Callable[[], Awaitable[None]]] = []

    async def update(self) -> None:
        """Fetch fresh runtime status and campaign board in a single batch."""
        self.status = await neurocomment_runtime_status()
        self.board = await load_neurocomment_board(self.campaign_id)
        for cb in list(self.on_reload_callbacks):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as exc:  # noqa: BLE001
                await log_event(
                    "ERROR",
                    "neurocomment_ui_reload_callback_failed",
                    extra={"error_type": type(exc).__name__, "error_message": str(exc)},
                )


async def count_ready_accounts_across_active_campaigns() -> int:
    """Count ready accounts across all active campaigns to avoid start block issues."""
    campaign_list = await list_campaigns()
    active_campaigns = [c for c in campaign_list.campaigns if c.status == "active"]
    ready_count = 0
    seen_accounts = set()
    for campaign in active_campaigns:
        board = await load_neurocomment_board(campaign.campaign_id)
        if board:
            for acc in board.accounts:
                if acc.health == "ready" and acc.account_id not in seen_accounts:
                    seen_accounts.add(acc.account_id)
                    ready_count += 1
    return ready_count


def _build_header() -> None:  # pragma: no cover
    with ui.row().classes(TOP_BAR_CLASSES):
        render_nav("/neurocomment")


async def render_neurocomment_page() -> None:  # pragma: no cover
    # Lazy imports: the sibling render modules import this module's pure helpers at
    # module level, so importing them here (not at top) avoids an import cycle.
    from features.neurocomment._engine_panel import render_engine_panel  # noqa: PLC0415
    from features.neurocomment._explainer import render_how_it_works  # noqa: PLC0415
    from features.neurocomment._setup import (  # noqa: PLC0415
        render_create_campaign,
        render_setup,
        render_warmed_accounts,
    )
    from features.neurocomment._workview import render_work_view  # noqa: PLC0415

    ui.query("body").classes("bg-slate-50 dark:bg-slate-900 text-slate-950 dark:text-slate-50")
    _build_header()
    with ui.column().classes("w-full max-w-[1200px] mx-auto p-4 gap-4"):
        ui.label("Нейрокомментинг").classes("text-xl font-semibold")
        # Fleet-wide overview of warmed accounts, at the very top.
        await render_warmed_accounts()

        campaign_list = await list_campaigns()
        if not campaign_list.campaigns:
            # No campaign yet → just the create form (open) + the explainer.
            await render_create_campaign(on_created=_reload_page, expanded=True)
            render_how_it_works()
            return

        # Shared Page Context for polling data
        ctx = PageContext(campaign_list.campaigns[-1].campaign_id)
        await ctx.update()

        # Engine panel is now global and rendered at the top level
        await render_engine_panel(ctx)

        @ui.refreshable
        async def setup_section() -> None:
            await render_setup(ctx.campaign_id)

        @ui.refreshable
        async def work_section() -> None:
            await render_work_view(ctx)

        def on_switch() -> None:
            ctx.campaign_id = switcher.value
            ctx.on_reload_callbacks.clear()  # prevent stale callback accumulation
            setup_section.refresh()
            work_section.refresh()

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
        # «Новая кампания» (global) + «Настройка» (per-campaign) side by side.
        with ui.row().classes("w-full gap-4 items-start flex-wrap"):
            with ui.column().classes("flex-1 min-w-[340px]"):
                await render_create_campaign(on_created=_reload_page, expanded=False)
            with ui.column().classes("flex-1 min-w-[340px]"):
                await setup_section()
        await work_section()
        render_how_it_works()

        # Page-level timer coordinates all polling
        timer = ui.timer(4.0, ctx.update)
        context.client.on_disconnect(lambda: timer.cancel(with_current_invocation=True))


def _reload_page() -> None:  # pragma: no cover
    ui.navigate.to("/neurocomment")
