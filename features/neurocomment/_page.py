"""Neurocomment page renderer — setup section + work view (issue #119).

UI-thin (non-negotiable #1), fully ``pragma: no cover``. Pure-display label maps
(``_CHANNEL_STATUS_RU`` / ``_HEALTH_RU``) are the only testable surface and are
covered in ``tests/features/test_neurocomment_labels.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from nicegui import ui

from core.config import settings
from features.shared import TOP_BAR_CLASSES, render_nav
from schemas.neurocomment import CampaignCreate
from services.accounts import list_accounts
from services.neurocomment import (
    assign_account_to_campaign,
    create_campaign,
    deactivate_channel,
    link_channel,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
    onboard_campaign,
    remove_account_from_campaign,
    start_neurocomment,
    stop_neurocomment,
)

if TYPE_CHECKING:
    from schemas.challenge import ChallengeRow
    from schemas.neurocomment import CampaignList

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


def _build_header() -> None:  # pragma: no cover
    with ui.row().classes(TOP_BAR_CLASSES):
        render_nav("/neurocomment")


async def render_neurocomment_page() -> None:  # pragma: no cover
    ui.query("body").classes("bg-slate-50 text-slate-950")
    _build_header()
    with ui.column().classes("w-full max-w-[1200px] mx-auto p-4 gap-4"):
        ui.label("Нейрокомментинг").classes("text-xl font-semibold")
        await _render_create_campaign(on_created=_reload_page)
        campaign_list = await list_campaigns()
        if not campaign_list.campaigns:
            return

        # Campaign switcher: pick which campaign to set up / work. Defaults to the
        # most recent campaign (the prior single-campaign default); switching
        # re-renders the section below.
        @ui.refreshable
        async def section() -> None:
            # Setup + work view for the selected campaign. The work view owns its
            # own poll timer, recreated (and the old one dropped) on each switch.
            # Lazy import: _workview imports this module's helpers at module level.
            from features.neurocomment._workview import render_work_view  # noqa: PLC0415

            await _render_setup(switcher.value)
            await render_work_view(switcher.value)

        def on_switch() -> None:
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
        await _render_runtime_controls()
        await section()


def _reload_page() -> None:  # pragma: no cover
    ui.navigate.to("/neurocomment")


async def _render_create_campaign(on_created) -> None:  # noqa: ANN001  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-3"):
        ui.label("Новая кампания").classes("text-base font-semibold")
        name = ui.input(label="Название").props("dense outlined").classes("w-full")
        prompt = (
            ui.textarea(
                label="Промпт (упоминание продукта живёт здесь)",
                placeholder="Например: ненавязчиво упомяни сервис X как читатель…",
            )
            .props("dense outlined autogrow")
            .classes("w-full")
        )
        ui.label(
            f"Комментарий не длиннее {settings.neurocomment.comment_max_words} слов "
            "(настраивается в конфиге).",
        ).classes("text-xs text-slate-500")

        async def on_create() -> None:
            if not (name.value or "").strip() or not (prompt.value or "").strip():
                ui.notify("Заполните название и промпт", type="warning")
                return
            data = CampaignCreate(name=name.value.strip(), prompt=prompt.value.strip())
            await create_campaign(data)
            ui.notify("Кампания создана", type="positive")
            on_created()

        ui.button("Создать кампанию", icon="add", on_click=on_create).props("color=primary")


async def _render_setup(campaign_id: str) -> None:  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-4"):
        ui.label("Настройка кампании").classes("text-base font-semibold")
        await _render_channel_pool(campaign_id)
        await _render_account_picker(campaign_id)
        await _render_actions(campaign_id)


async def _render_channel_pool(campaign_id: str) -> None:  # pragma: no cover
    ui.label("Каналы").classes("text-sm font-medium")
    channels_box = ui.column().classes("w-full gap-1")
    channel_input = (
        ui.input(label="Добавить канал", placeholder="@channel")
        .props("dense outlined")
        .classes("w-full")
    )

    async def refresh() -> None:
        channels_box.clear()
        links = (await list_campaign_channels(campaign_id)).links
        with channels_box:
            if not links:
                ui.label("Каналов пока нет").classes("text-xs text-slate-400")
            for link in links:
                with ui.row().classes("w-full items-center justify-between"):
                    ui.label(link.channel).classes("text-sm")
                    ui.button(
                        icon="delete",
                        on_click=lambda _e=None, ch=link.channel: on_remove(ch),
                    ).props("flat dense round color=grey-7")

    async def on_add() -> None:
        channel = (channel_input.value or "").strip()
        if not channel:
            ui.notify("Введите канал", type="warning")
            return
        outcome = await link_channel(campaign_id, channel)
        if outcome.status == "already_assigned":
            ui.notify("Канал уже активен в другой кампании", type="warning")
            return
        channel_input.value = ""
        await refresh()

    async def on_remove(channel: str) -> None:
        await deactivate_channel(campaign_id, channel)
        await refresh()

    ui.button("Добавить канал", icon="add", on_click=on_add).props("color=primary")
    await refresh()


async def _render_account_picker(campaign_id: str) -> None:  # pragma: no cover
    ui.label("Аккаунты").classes("text-sm font-medium")
    accounts = (await list_accounts()).accounts
    assigned = {link.account_id for link in (await list_campaign_accounts(campaign_id)).links}

    async def on_toggle(account_id: str, checked: bool) -> None:  # noqa: FBT001
        if checked:
            await assign_account_to_campaign(campaign_id, account_id)
            ui.notify("Аккаунт добавлен в кампанию", type="positive")
        else:
            await remove_account_from_campaign(campaign_id, account_id)
            ui.notify("Аккаунт убран из кампании", type="info")

    with ui.column().classes("w-full gap-1"):
        if not accounts:
            ui.label("Сначала добавьте аккаунты на странице «Аккаунты»").classes(
                "text-xs text-slate-400",
            )
        for acc in accounts:
            ui.checkbox(
                acc.label or acc.account_id,
                value=acc.account_id in assigned,
                on_change=lambda e, aid=acc.account_id: on_toggle(aid, e.value),
            ).props("dense")


async def _render_actions(campaign_id: str) -> None:  # pragma: no cover
    async def on_onboard() -> None:
        result = await onboard_campaign(campaign_id)
        ready = sum(1 for o in result.outcomes if o.state == "ready")
        ui.notify(f"Онбординг: готово пар — {ready} из {len(result.outcomes)}", type="info")

    ui.button("Онбординг", icon="how_to_reg", on_click=on_onboard).props("outline")


async def _render_runtime_controls() -> None:  # pragma: no cover
    # Fleet-wide runtime: ONE listener reads posts across all active campaigns and
    # the engine routes each post to its own campaign. Rendered once (not per
    # campaign) so the controls match the single-listener runtime.
    accounts = (await list_accounts()).accounts
    with ui.card().classes("w-full p-4 gap-2"):
        ui.label("Запуск нейрокомментинга (весь флот)").classes("text-base font-semibold")
        listener_select = (
            ui.select(
                {acc.account_id: (acc.label or acc.account_id) for acc in accounts},
                label="Аккаунт-слушатель",
            )
            .props("dense outlined")
            .classes("w-full max-w-[400px]")
        )

        async def on_start() -> None:
            listener = listener_select.value
            if not listener:
                ui.notify("Выберите аккаунт-слушатель", type="warning")
                return
            await start_neurocomment(listener)
            ui.notify("Нейрокомментинг запущен", type="positive")

        async def on_stop() -> None:
            await stop_neurocomment()
            ui.notify("Нейрокомментинг остановлен", type="info")

        with ui.row().classes("w-full items-center gap-2"):
            ui.button("Запустить", icon="play_arrow", on_click=on_start).props("color=positive")
            ui.button("Остановить", icon="stop", on_click=on_stop).props("color=negative outline")
        ui.label(
            "Один слушатель на все активные кампании; движок раздаёт посты по их "
            "кампаниям. «Остановить» останавливает весь флот.",
        ).classes("text-xs text-slate-500")
