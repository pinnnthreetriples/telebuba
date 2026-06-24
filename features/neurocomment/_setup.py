"""Neurocomment setup rendering — campaign create + channel/account configuration.

Split out of ``_page`` (page redesign) to keep each module under the file-size
budget and to separate the static configuration UI from the live engine panel.
UI-thin per non-negotiable #1: every function carries ``# pragma: no cover`` and
delegates to ``services.neurocomment`` / ``services.accounts``. The form is wrapped
in collapsible ``ui.expansion`` blocks and channels render as compact chips so the
configuration folds away once a campaign is set up (the redesign's compactness goal).
"""

from __future__ import annotations

from nicegui import ui

from core.config import settings
from schemas.neurocomment import CampaignCreate
from services.accounts import list_accounts
from services.neurocomment import (
    assign_account_to_campaign,
    create_campaign,
    deactivate_channel,
    link_channel,
    list_campaign_accounts,
    list_campaign_channels,
    onboard_campaign,
    remove_account_from_campaign,
)
from services.warming import list_warmed_accounts


async def render_warmed_accounts() -> None:  # pragma: no cover
    """Top fleet-wide overview: which accounts are warmed enough to commentate.

    Read-only — accounts appear here as they cross ``warmed_min_days`` on the warming
    page. Per-campaign assignment still happens in «Настройка» below.
    """
    min_days = settings.neurocomment.warmed_min_days
    warmed = (await list_warmed_accounts(min_days)).accounts
    with ui.card().classes("w-full p-3 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("local_fire_department").classes("text-amber-500")
            ui.label("Прогретые аккаунты").classes("text-sm font-semibold")
            ui.label(f"от {min_days} дн").classes("text-xs text-slate-400").tooltip(
                f"Аккаунты с прогревом ≥ {min_days} дней пригодны для комментирования",
            )
        if not warmed:
            ui.label("Нет прогретых аккаунтов — прогрейте их на странице «Прогрев».").classes(
                "text-xs text-slate-400",
            )
            return
        with ui.row().classes("w-full gap-2 flex-wrap items-center"):
            for acc in warmed:
                with ui.row().classes(
                    "items-center gap-1 rounded-full bg-emerald-50 "
                    "border border-emerald-100 pl-3 pr-2 py-0.5",
                ):
                    ui.label(acc.label).classes("text-xs text-emerald-800")
                    ui.label(f"{acc.warming_days}д").classes("text-[10px] text-emerald-500")


async def render_create_campaign(on_created, *, expanded: bool) -> None:  # noqa: ANN001  # pragma: no cover
    """Collapsible «Новая кампания» form; open by default only when none exist yet."""
    expansion = ui.expansion("Новая кампания", icon="add_circle", value=expanded)
    expansion.classes("w-full").props("dense")
    with expansion, ui.column().classes("w-full p-2 gap-3"):
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


async def render_setup(campaign_id: str) -> None:  # pragma: no cover
    """Collapsible «Настройка» block: channel pool + account picker + onboard."""
    expansion = ui.expansion("Настройка: каналы и аккаунты", icon="tune", value=True)
    expansion.classes("w-full").props("dense")
    with expansion, ui.column().classes("w-full p-2 gap-4"):
        await _render_channel_pool(campaign_id)
        await _render_account_picker(campaign_id)
        await _render_actions(campaign_id)


async def _render_channel_pool(campaign_id: str) -> None:  # pragma: no cover
    ui.label("Каналы").classes("text-sm font-medium")
    chips_box = ui.row().classes("w-full gap-2 flex-wrap items-center")

    async def on_remove(channel: str) -> None:
        await deactivate_channel(campaign_id, channel)
        await refresh()

    async def refresh() -> None:
        chips_box.clear()
        links = (await list_campaign_channels(campaign_id)).links
        with chips_box:
            if not links:
                ui.label("Каналов пока нет").classes("text-xs text-slate-400")
            for link in links:
                with ui.row().classes(
                    "items-center gap-1 rounded-full bg-slate-100 pl-3 pr-1 py-0.5",
                ):
                    ui.label(link.channel).classes("text-xs text-slate-700")
                    ui.button(
                        icon="close",
                        on_click=lambda _e=None, ch=link.channel: on_remove(ch),
                    ).props("flat dense round size=sm color=grey-7")

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

    with ui.row().classes("w-full items-center gap-2"):
        channel_input = ui.input(placeholder="@channel").props("dense outlined").classes("flex-1")
        ui.button(icon="add", on_click=on_add).props("color=primary dense")
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

    if not accounts:
        ui.label("Сначала добавьте аккаунты на странице «Аккаунты»").classes(
            "text-xs text-slate-400",
        )
        return
    with ui.row().classes("w-full gap-x-4 gap-y-1 flex-wrap"):
        for acc in accounts:
            ui.checkbox(
                acc.label or acc.account_id,
                value=acc.account_id in assigned,
                on_change=lambda e, aid=acc.account_id: on_toggle(aid, e.value),
            ).props("dense")


async def _render_actions(campaign_id: str) -> None:  # pragma: no cover
    async def on_onboard() -> None:
        # Onboarding can take many seconds (pre-join + readiness probe per pair); give
        # immediate feedback + a button spinner so the operator doesn't think it hung.
        ui.notify("Онбординг запущен…", type="info")
        button.props("loading")
        try:
            result = await onboard_campaign(campaign_id)
        finally:
            button.props(remove="loading")
        ready = sum(1 for o in result.outcomes if o.state == "ready")
        ui.notify(f"Онбординг: готово пар — {ready} из {len(result.outcomes)}", type="info")

    button = ui.button("Онбординг", icon="how_to_reg", on_click=on_onboard).props("outline")
