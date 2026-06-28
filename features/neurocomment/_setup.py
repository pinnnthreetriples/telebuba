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
    """Top fleet-wide overview: which accounts are promoted to neurocomment.

    Read-only — accounts only appear here after the operator presses
    «Переместить в нейрокомментинг» on the warming card (which also requires at least
    ``warmed_min_days`` of warming). Per-campaign assignment still happens in
    «Настройка» below.
    """
    min_days = settings.neurocomment.warmed_min_days
    warmed = (await list_warmed_accounts(min_days)).accounts
    with ui.element("div").classes("tb-card w-full").style("padding:13px 14px"):
        with ui.row().classes("w-full items-center gap-2 flex-nowrap").style("margin-bottom:8px"):
            ui.html(
                '<div style="width:28px;height:28px;border-radius:8px;background:#FFFBEF;'
                'color:#9A7B22;display:flex;align-items:center;justify-content:center">'
                '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2'
                "-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5"
                ' 2.5 0 0 0 2.5 2.5z"/></svg></div>',
            )
            ui.label("Прогретые аккаунты").classes("tb-title")
            ui.label(f"от {min_days} дн").classes("tb-muted").style("font-size:11px").tooltip(
                f"Появляются после нажатия «Переместить в нейрокомментинг» на "
                f"карточке прогрева (минимум {min_days} дней прогрева).",
            )
        if not warmed:
            ui.label(
                "Нет аккаунтов в нейрокомментинге — прогрейте аккаунт, затем нажмите "
                "«Переместить в нейрокомментинг» на карточке.",
            ).classes("tb-muted").style("font-size:11.5px")
            return
        with ui.row().classes("w-full gap-2 flex-wrap items-center"):
            for acc in warmed:
                ui.html(
                    '<span style="display:inline-flex;align-items:center;gap:6px;'
                    "background:#DDF7E9;border:1px solid #CFEBD8;border-radius:9999px;"
                    'padding:4px 11px">'
                    f'<span style="font-size:12px;color:#0B6B37">{acc.label}</span>'
                    f'<span style="font-size:10px;color:#3F8A5E">{acc.warming_days}д</span></span>',
                )


async def render_create_campaign(on_created, *, expanded: bool) -> None:  # noqa: ANN001, ARG001  # pragma: no cover
    """«Новая кампания» card; styled to the design palette."""
    with ui.element("div").classes("tb-card w-full").style("padding:16px 18px"):
        with ui.row().classes("w-full items-center gap-2 flex-nowrap").style("margin-bottom:10px"):
            ui.html(
                '<div style="width:28px;height:28px;border-radius:8px;background:#E8F0FF;'
                'color:#0066FF;display:flex;align-items:center;justify-content:center">'
                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                ' stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></div>',
            )
            ui.label("Новая кампания").classes("tb-title")
        with ui.column().classes("w-full gap-2"):
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
            ).classes("tb-muted").style("font-size:11.5px")

            async def on_create() -> None:
                if not (name.value or "").strip() or not (prompt.value or "").strip():
                    ui.notify("Заполните название и промпт", type="warning")
                    return
                data = CampaignCreate(name=name.value.strip(), prompt=prompt.value.strip())
                await create_campaign(data)
                ui.notify("Кампания создана", type="positive")
                on_created()

            create = ui.button("Создать кампанию", on_click=on_create)
            create.props("flat no-caps").classes("tb-btn tb-btn-primary w-full").style(
                "margin-top:2px",
            )
            with create:
                ui.icon("add").classes("text-base").style("order:-1;margin-right:-2px")


async def render_setup(campaign_id: str) -> None:  # pragma: no cover
    """«Настройка» card: channel pool + account picker + onboard."""
    with ui.element("div").classes("tb-card w-full").style("padding:16px 18px"):
        with ui.row().classes("w-full items-center gap-2 flex-nowrap").style("margin-bottom:10px"):
            ui.html(
                '<div style="width:28px;height:28px;border-radius:8px;background:#EEF4FF;'
                'color:#0066FF;display:flex;align-items:center;justify-content:center">'
                '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                ' stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>'
                "</svg></div>",
            )
            ui.label("Настройка: каналы и аккаунты").classes("tb-title")
        with ui.column().classes("w-full gap-3"):
            await _render_channel_pool(campaign_id)
            await _render_account_picker(campaign_id)
            await _render_actions(campaign_id)


async def _render_channel_pool(campaign_id: str) -> None:  # pragma: no cover
    ui.label("Каналы").classes("tb-uplabel")
    chips_box = ui.row().classes("w-full gap-2 flex-wrap items-center")

    async def on_remove(channel: str) -> None:
        await deactivate_channel(campaign_id, channel)
        await refresh()

    async def refresh() -> None:
        chips_box.clear()
        links = (await list_campaign_channels(campaign_id)).links
        with chips_box:
            if not links:
                ui.label("Каналов пока нет").classes("tb-muted").style("font-size:11.5px")
            for link in links:
                with ui.element("span").style(
                    "display:inline-flex;align-items:center;gap:4px;background:#F4F3F0;"
                    "border:1px solid #E6E5E3;border-radius:9999px;padding:3px 4px 3px 11px",
                ):
                    ui.html(f'<span style="font-size:12px;color:#3A3A3A">{link.channel}</span>')
                    ui.button(
                        icon="close",
                        on_click=lambda _e=None, ch=link.channel: on_remove(ch),
                    ).props("flat dense round size=sm").style("color:#B5B3AE")

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

    with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
        channel_input = (
            ui.input(placeholder="t.me/канал или @канал").props("dense outlined").classes("flex-1")
        )
        add = ui.button(icon="add", on_click=on_add)
        add.props("flat dense round").classes("tb-icon-btn").style(
            "background:#0066FF;color:#fff;border-color:#0066FF",
        )
    await refresh()


async def _render_account_picker(campaign_id: str) -> None:  # pragma: no cover
    ui.label("Аккаунты").classes("tb-uplabel")
    accounts = (await list_accounts()).accounts
    assigned = {link.account_id for link in (await list_campaign_accounts(campaign_id)).links}
    min_days = settings.neurocomment.warmed_min_days
    warmed = (await list_warmed_accounts(min_days)).accounts
    warmed_ids = {acc.account_id for acc in warmed}

    async def on_toggle(account_id: str, checked: bool) -> None:  # noqa: FBT001
        if checked:
            await assign_account_to_campaign(campaign_id, account_id)
            ui.notify("Аккаунт добавлен в кампанию", type="positive")
        else:
            await remove_account_from_campaign(campaign_id, account_id)
            ui.notify("Аккаунт убран из кампании", type="info")

    if not accounts:
        ui.label("Сначала добавьте аккаунты на странице «Аккаунты»").classes("tb-muted").style(
            "font-size:11.5px",
        )
        return
    with ui.row().classes("w-full gap-x-4 gap-y-1 flex-wrap"):
        for acc in accounts:
            is_warmed = acc.account_id in warmed_ids
            label = acc.label or acc.account_id
            if is_warmed:
                label += " 🔥"
            checkbox = ui.checkbox(
                label,
                value=acc.account_id in assigned,
                on_change=lambda e, aid=acc.account_id: on_toggle(aid, e.value),
            ).props("dense")
            if is_warmed:
                checkbox.classes("text-emerald-600 dark:text-emerald-400 font-medium")
                checkbox.tooltip(f"Прогрет ({min_days}+ дней)")


async def _render_actions(campaign_id: str) -> None:  # pragma: no cover
    with ui.column().classes("w-full gap-2"):

        async def on_onboard() -> None:
            log_panel.set_visibility(True)
            log_widget.clear()
            log_widget.push("Инициализация онбординга...")
            ui.notify("Онбординг запущен…", type="info")
            button.props("loading")
            try:
                result = await onboard_campaign(campaign_id, on_progress=log_widget.push)
            finally:
                button.props(remove="loading")
            ready = sum(1 for o in result.outcomes if o.state == "ready")
            ui.notify(f"Онбординг: готово пар — {ready} из {len(result.outcomes)}", type="info")

        with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
            button = ui.button("Онбординг", on_click=on_onboard)
            button.props("flat no-caps").classes("tb-btn tb-btn-white")
            with button:
                ui.icon("how_to_reg").classes("text-base").style("order:-1;margin-right:-2px")
            log_toggle = (
                ui.button(
                    icon="terminal",
                    on_click=lambda: log_panel.set_visibility(not log_panel.visible),
                )
                .props("flat round dense")
                .classes("tb-icon-btn")
            )
            log_toggle.tooltip("Показать/скрыть лог онбординга")
            help_icon = (
                ui.icon("help_outline")
                .style("color:#9A9893;font-size:18px")
                .classes(
                    "cursor-help",
                )
            )
            help_icon.tooltip(
                "Онбординг готовит аккаунты к работе: автоматически вступает в группы "
                "обсуждения выбранных каналов, решает приветственную капчу и проверяет "
                "готовность к публикации комментариев."
            )

        with (
            ui.column()
            .classes("w-full gap-1")
            .style(
                "border:1px solid #2b2b2e;border-radius:10px;background:#16161A;padding:10px 12px",
            ) as log_panel
        ):
            log_panel.set_visibility(False)
            ui.label("Лог онбординга").style(
                "font-size:11px;color:#9A9893;font-family:'JetBrains Mono',monospace",
            )
            log_widget = (
                ui.log()
                .classes("w-full h-32 bg-transparent border-0")
                .style(
                    "font-family:'JetBrains Mono',monospace;font-size:11px;color:#C9C9CE",
                )
            )
