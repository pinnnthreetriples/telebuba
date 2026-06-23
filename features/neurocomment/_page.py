"""Neurocomment page renderer — setup section + work view (issue #119).

UI-thin (non-negotiable #1), fully ``pragma: no cover``. Pure-display label maps
(``_CHANNEL_STATUS_RU`` / ``_HEALTH_RU``) are the only testable surface and are
covered in ``tests/features/test_neurocomment_labels.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import context, ui

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    create_campaign,
    deactivate_channel,
    link_channel_to_campaign,
    list_accounts,
    list_campaign_accounts,
    list_campaign_channels,
    list_campaigns,
    remove_account_from_campaign,
)
from core.repositories.neurocomment import ChannelAlreadyAssignedError
from schemas.neurocomment import CampaignCreate
from services.neurocomment import (
    load_neurocomment_board,
    onboard_campaign,
    start_neurocomment,
    stop_neurocomment,
)

if TYPE_CHECKING:
    from schemas.neurocomment import CampaignList, NeurocommentBoard

_BOARD_POLL_SECONDS = 4.0

# Pure-display maps (the page's only unit-tested logic).
_CHANNEL_STATUS_RU: dict[str, str] = {
    "ready": "Готов",
    "comments_off": "Комментарии выключены",
    "join_by_request": "Вступление по заявке",
    "captcha_gated": "Капча / блок записи",
    "throttled": "Лимит исчерпан",
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
    with (
        ui.row().classes(
            "w-full items-center justify-between px-4 py-2 bg-white "
            "text-slate-950 border-b border-slate-200",
        ),
        ui.row().classes("items-center gap-4"),
    ):
        ui.label("Telebuba").classes("text-lg font-semibold")
        ui.link("Аккаунты", "/").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")
        ui.link("Прогрев", "/warming").classes(
            "text-sm text-slate-600 hover:text-slate-900 no-underline",
        )
        ui.link("Нейрокомментинг", "/neurocomment").classes(
            "text-sm font-medium text-slate-900 no-underline",
        )
        ui.link("Логи", "/logs").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")


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
            await _render_setup(switcher.value)
            await _render_work_view(switcher.value)

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
        listener_select = await _render_account_picker(campaign_id)
        await _render_actions(campaign_id, listener_select)


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
        try:
            await link_channel_to_campaign(campaign_id, channel)
        except ChannelAlreadyAssignedError:
            ui.notify("Канал уже активен в другой кампании", type="warning")
            return
        channel_input.value = ""
        await refresh()

    async def on_remove(channel: str) -> None:
        await deactivate_channel(campaign_id, channel)
        await refresh()

    ui.button("Добавить канал", icon="add", on_click=on_add).props("color=primary")
    await refresh()


async def _render_account_picker(campaign_id: str) -> ui.select:  # pragma: no cover
    ui.label("Аккаунты").classes("text-sm font-medium")
    accounts = (await list_accounts()).accounts
    assigned = {link.account_id for link in (await list_campaign_accounts(campaign_id)).links}
    options = {acc.account_id: (acc.label or acc.account_id) for acc in accounts}

    async def on_toggle(account_id: str, checked: bool) -> None:  # noqa: FBT001
        if checked:
            await assign_account_to_campaign(campaign_id, account_id)
            assigned.add(account_id)
            ui.notify("Аккаунт добавлен в кампанию", type="positive")
        else:
            await remove_account_from_campaign(campaign_id, account_id)
            assigned.discard(account_id)
            ui.notify("Аккаунт убран из кампании", type="info")
        # Keep the listener choices in sync so a just-(un)assigned account
        # appears/disappears without a page reload.
        listener_select.set_options({aid: options.get(aid, aid) for aid in assigned})

    with ui.column().classes("w-full gap-1"):
        if not accounts:
            ui.label("Сначала добавьте аккаунты на странице «Аккаунты»").classes(
                "text-xs text-slate-400",
            )
        for acc in accounts:
            ui.checkbox(
                options[acc.account_id],
                value=acc.account_id in assigned,
                on_change=lambda e, aid=acc.account_id: on_toggle(aid, e.value),
            ).props("dense")

    ui.label("Аккаунт-слушатель (читает посты и раздаёт их на комментирование)").classes(
        "text-sm font-medium mt-2",
    )
    # Listener choices = the campaign's assigned accounts (the listener must be a
    # serving account). Empty until at least one account is assigned; on_toggle
    # keeps it in sync as accounts are added.
    listener_select = (
        ui.select({aid: options.get(aid, aid) for aid in assigned}, label="Слушатель")
        .props("dense outlined")
        .classes("w-full max-w-[400px]")
    )
    return listener_select


async def _render_actions(campaign_id: str, listener_select) -> None:  # noqa: ANN001  # pragma: no cover
    with ui.column().classes("w-full gap-1"):

        async def on_onboard() -> None:
            result = await onboard_campaign(campaign_id)
            ready = sum(1 for o in result.outcomes if o.state == "ready")
            ui.notify(f"Онбординг: готово пар — {ready} из {len(result.outcomes)}", type="info")

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
            ui.button("Онбординг", icon="how_to_reg", on_click=on_onboard).props("outline")
            ui.button("Запустить", icon="play_arrow", on_click=on_start).props("color=positive")
            ui.button("Остановить", icon="stop", on_click=on_stop).props("color=negative outline")
        # The neurocomment runtime is one fleet-wide listener over the union of all
        # active campaigns' channels (engine routes each post to its own campaign).
        # Make that explicit so "Остановить" isn't mistaken for campaign-scoped.
        ui.label(
            "Онбординг — для выбранной кампании. Запуск и остановка — на весь флот: "
            "нейрокомментинг использует одного слушателя на все активные кампании, "
            "поэтому «Остановить» останавливает все кампании сразу.",
        ).classes("text-xs text-slate-500")


async def _render_work_view(campaign_id: str) -> None:  # pragma: no cover
    board = await load_neurocomment_board(campaign_id)
    container = ui.column().classes("w-full gap-4")

    @ui.refreshable
    def render() -> None:
        _render_board(board)

    async def reload() -> None:
        nonlocal board
        board = await load_neurocomment_board(campaign_id)
        render.refresh()

    with container:
        render()
    board_timer = ui.timer(_BOARD_POLL_SECONDS, reload)
    context.client.on_disconnect(lambda: board_timer.cancel(with_current_invocation=True))


def _render_board(board: NeurocommentBoard | None) -> None:  # pragma: no cover
    if board is None:
        ui.label("Кампания не найдена").classes("text-sm text-slate-400")
        return
    with ui.row().classes("w-full gap-4 items-start flex-wrap"):
        _render_channels_panel(board)
        _render_accounts_panel(board)


def _render_channels_panel(board: NeurocommentBoard) -> None:  # pragma: no cover
    with ui.card().classes("w-[360px] p-4 gap-2"):
        ui.label("Каналы").classes("text-base font-semibold")
        if not board.channels:
            ui.label("Каналов пока нет").classes("text-xs text-slate-400")
        for row in board.channels:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(row.channel).classes("text-sm")
                with ui.row().classes("items-center gap-2"):
                    ui.label(f"{row.ready_accounts}/{row.total_accounts}").classes(
                        "text-xs text-slate-500",
                    )
                    ui.badge(channel_status_label(row.status)).props("color=blue-grey")


def _render_accounts_panel(board: NeurocommentBoard) -> None:  # pragma: no cover
    with ui.column().classes("flex-1 min-w-[360px] gap-3"):
        if not board.accounts:
            ui.label("Аккаунтов в кампании пока нет").classes("text-xs text-slate-400")
        for card in board.accounts:
            _render_account_card(card)


def _render_account_card(card) -> None:  # noqa: ANN001  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(card.label).classes("text-sm font-semibold")
            ui.badge(health_label(card.health)).props(
                f"color={'green' if card.health == 'ready' else 'red'}",
            )
        ui.label(
            f"Комментариев за час: {card.comments_last_hour}/{card.max_comments_per_hour} · "
            f"за сутки: {card.comments_today}",
        ).classes("text-xs text-slate-600")
        ui.label(
            f"Доверие: {card.trust_score} ({card.trust_band})"
            + (f" · спам: {card.spam_status}" if card.spam_status else ""),
        ).classes("text-xs text-slate-500")
        if card.last_comment_at:
            stamp = card.last_comment_at[:16].replace("T", " ")
            ui.label(f"Последний комментарий: {stamp}").classes("text-xs text-slate-400")
