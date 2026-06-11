"""NiceGUI Warming page.

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.warming`` function, and re-renders. No business logic here.

Layout:
- **Settings** — Gemini API key, inter-account chat toggle, reactions toggle.
- **Channels** — add unlimited links/usernames, list with per-row delete.
- **Kanban** — drag accounts between *Idle* and *Warming*; dropping into the
  Warming column starts the loop, dropping back into Idle stops it.
- **Activity log** — live, colour-coded (green/amber/red) feed of warming events.

Everything below is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually, the logic it calls is unit-tested.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from nicegui import ui

from schemas.logs import LogFilter
from schemas.warming import (
    AddChannelsRequest,
    RemoveChannelRequest,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingSettingsUpdate,
)
from services.logs import load_logs_page
from services.warming import (
    add_channels,
    load_board,
    remove_channel,
    save_settings,
    start_warming,
    stop_warming,
)

if TYPE_CHECKING:
    from schemas.warming import WarmingAccountState, WarmingBoardState

_BOARD_POLL_SECONDS = 4.0
_LOG_POLL_SECONDS = 2.0
_LOG_LIMIT = 40

_HEALTH_DOT = {
    "ok": "bg-green-500",
    "warn": "bg-amber-500",
    "fail": "bg-red-500",
    "idle": "bg-slate-400",
}
_STATE_LABEL = {
    "idle": "Idle",
    "active": "Warming",
    "sleeping": "Sleeping",
    "flood_wait": "Flood wait",
    "error": "Error",
}
_STATE_BADGE = {
    "idle": "text-slate-600 bg-slate-100",
    "active": "text-green-700 bg-green-100",
    "sleeping": "text-amber-700 bg-amber-100",
    "flood_wait": "text-amber-800 bg-amber-100",
    "error": "text-red-700 bg-red-100",
}
_LOG_ROW_BORDER = {
    "success": "border-green-500",
    "warning": "border-amber-500",
    "error": "border-red-500",
}

_WARMING_CSS = """
@keyframes tb-pulse-ring {
    0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.45); }
    70% { box-shadow: 0 0 0 8px rgba(34,197,94,0); }
    100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
}
.tb-active { animation: tb-pulse-ring 1.8s infinite; }
@keyframes tb-fade-in {
    from { opacity: 0; transform: translateY(-4px); }
    to { opacity: 1; transform: translateY(0); }
}
.tb-log-row { animation: tb-fade-in 0.35s ease-out; }
.tb-dropzone { transition: background-color 0.2s ease, border-color 0.2s ease; }
"""


def register_warming_page() -> None:  # pragma: no cover
    @ui.page("/warming", title="Telebuba — Warming")
    async def warming_page() -> None:
        await _render_warming_page()


def _build_header() -> None:  # pragma: no cover
    with (
        ui.row().classes(
            "w-full items-center justify-between px-4 py-2 bg-white "
            "text-slate-950 border-b border-slate-200",
        ),
        ui.row().classes("items-center gap-4"),
    ):
        ui.label("Telebuba").classes("text-lg font-semibold")
        ui.link("Accounts", "/").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")
        ui.link("Warming", "/warming").classes("text-sm font-medium text-slate-900 no-underline")
        ui.link("Logs", "/logs").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")


async def _render_warming_page() -> None:  # pragma: no cover
    ui.add_head_html(f"<style>{_WARMING_CSS}</style>")
    ui.query("body").classes("bg-slate-50 text-slate-950")
    _build_header()

    drag: dict[str, str | None] = {"account_id": None}

    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-4"):
        ui.label("Account warming").classes("text-xl font-semibold")
        with ui.row().classes("w-full gap-4 items-start flex-wrap"):
            await _render_settings_card()
            await _render_channels_card()

        @ui.refreshable
        async def render_board() -> None:
            board = await load_board()
            _render_board(board, drag, render_board.refresh)

        await render_board()
        ui.timer(_BOARD_POLL_SECONDS, render_board.refresh)

        await _render_activity_log()


def _settings_status(model: str, *, has_key: bool) -> str:  # pragma: no cover
    return f"Model {model} · AI key {'set' if has_key else 'missing'}"


async def _render_settings_card() -> None:  # pragma: no cover
    board = await load_board()
    current = board.settings
    placeholder = "key is set" if current.has_gemini_key else "paste key to enable AI chat"
    with ui.card().classes("w-[420px] p-4 gap-3"):
        ui.label("Settings").classes("text-base font-semibold")
        key_input = (
            ui.input(label="Gemini API key", placeholder=placeholder, password=True)
            .props("dense outlined clearable")
            .classes("w-full")
        )
        chat_switch = ui.switch("Accounts chat with each other", value=current.inter_account_chat)
        reactions_switch = ui.switch("Put reactions on posts", value=current.reactions_enabled)
        status = ui.label(
            _settings_status(current.gemini_model, has_key=current.has_gemini_key),
        ).classes("text-xs text-slate-500")

        async def on_save() -> None:
            raw_key = (key_input.value or "").strip()
            updated = await save_settings(
                WarmingSettingsUpdate(
                    inter_account_chat=bool(chat_switch.value),
                    reactions_enabled=bool(reactions_switch.value),
                    gemini_api_key=raw_key or None,
                ),
            )
            key_input.value = ""
            status.set_text(_settings_status(updated.gemini_model, has_key=updated.has_gemini_key))
            ui.notify("Settings saved", type="positive")

        ui.button("Save settings", icon="save", on_click=on_save).props("color=primary").classes(
            "w-full",
        )


async def _render_channels_card() -> None:  # pragma: no cover
    with ui.card().classes("w-[420px] p-4 gap-3"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Channels").classes("text-base font-semibold")
            count_badge = ui.label("0").classes(
                "text-xs px-2 py-1 rounded bg-slate-100 text-slate-700",
            )
        channels_input = (
            ui.textarea(
                label="Add channels",
                placeholder="@channel, https://t.me/channel — one per line or comma-separated",
            )
            .props("dense outlined autogrow")
            .classes("w-full")
        )
        channels_box = ui.column().classes("w-full gap-1 max-h-64 overflow-auto")

        async def refresh_list() -> None:
            channels = await load_board()
            count_badge.set_text(str(channels.channel_count))
            channels_box.clear()
            with channels_box:
                if not channels.channels.channels:
                    ui.label("No channels yet").classes("text-xs text-slate-400")
                for channel in channels.channels.channels:
                    _render_channel_row(channel.channel, refresh_list)

        async def on_add() -> None:
            raw = (channels_input.value or "").strip()
            if not raw:
                ui.notify("Enter at least one channel", type="warning")
                return
            result = await add_channels(AddChannelsRequest(raw=raw))
            channels_input.value = ""
            await refresh_list()
            ui.notify(f"{len(result.channels)} channels total", type="positive")

        ui.button("Add channels", icon="add", on_click=on_add).props("color=primary").classes(
            "w-full",
        )
        await refresh_list()


def _render_channel_row(channel: str, refresh_list) -> None:  # noqa: ANN001 # pragma: no cover
    with ui.row().classes(
        "w-full items-center justify-between px-2 py-1 rounded bg-slate-50 border border-slate-200",
    ):
        ui.label(channel).classes("text-sm truncate")

        async def on_delete() -> None:
            await remove_channel(RemoveChannelRequest(channel=channel))
            await refresh_list()

        ui.button(icon="delete", on_click=on_delete).props("flat dense color=grey-7").classes(
            "shrink-0",
        )


def _render_board(board: WarmingBoardState, drag: dict[str, str | None], refresh) -> None:  # noqa: ANN001 # pragma: no cover
    with ui.row().classes("w-full gap-4 items-stretch flex-wrap"):
        _render_column(
            "Idle",
            "idle",
            board.idle,
            "border-slate-300",
            drag,
            refresh,
        )
        _render_column(
            f"Warming · {board.active_count} active",
            "warming",
            board.warming,
            "border-green-400",
            drag,
            refresh,
        )


def _render_column(  # noqa: PLR0913 # pragma: no cover
    title: str,
    key: str,
    cards: list[WarmingAccountState],
    border: str,
    drag: dict[str, str | None],
    refresh,  # noqa: ANN001
) -> None:
    column = ui.column().classes(
        f"tb-dropzone flex-1 min-w-[320px] p-3 gap-2 rounded-lg border-2 border-dashed "
        f"{border} bg-white min-h-[240px]",
    )

    async def on_drop() -> None:
        account_id = drag["account_id"]
        drag["account_id"] = None
        if not account_id:
            return
        if key == "warming":
            await start_warming(StartWarmingRequest(account_id=account_id))
        else:
            await stop_warming(StopWarmingRequest(account_id=account_id))
        refresh()

    column.on("dragover.prevent", lambda: None)
    column.on("drop", on_drop)
    with column:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(title).classes("text-sm font-semibold text-slate-700")
            ui.label(str(len(cards))).classes(
                "text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600",
            )
        if not cards:
            ui.label("Drag accounts here").classes("text-xs text-slate-400 italic")
        for card in cards:
            _render_card(card, drag)


def _render_card(  # pragma: no cover
    card: WarmingAccountState,
    drag: dict[str, str | None],
) -> None:
    pulse = " tb-active" if card.state == "active" else ""
    element = (
        ui.card()
        .props("draggable")
        .classes(
            f"w-full p-3 gap-1 cursor-grab bg-white border border-slate-200 rounded-md{pulse}",
        )
    )
    element.on("dragstart", lambda aid=card.account_id: drag.update(account_id=aid))
    with element:
        with ui.row().classes("w-full items-center gap-2"):
            ui.element("div").classes(
                f"w-2.5 h-2.5 rounded-full {_HEALTH_DOT.get(card.health, 'bg-slate-400')}",
            )
            ui.label(card.label).classes("text-sm font-medium truncate flex-1")
            ui.label(_STATE_LABEL.get(card.state, card.state)).classes(
                f"text-[11px] px-2 py-0.5 rounded {_STATE_BADGE.get(card.state, '')}",
            )
        meta = f"cycles {card.cycles_completed}"
        if card.last_event:
            meta = f"{meta} · {card.last_event}"
        ui.label(meta).classes("text-[11px] text-slate-500 truncate")


async def _render_activity_log() -> None:  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("bolt").classes("text-amber-500")
            ui.label("Live activity").classes("text-base font-semibold")
        log_box = ui.column().classes("w-full gap-1 max-h-80 overflow-auto")

        async def refresh_log() -> None:
            state = await load_logs_page(LogFilter(limit=_LOG_LIMIT))
            log_box.clear()
            with log_box:
                if not state.entries:
                    ui.label("Waiting for activity…").classes("text-xs text-slate-400")
                for entry in state.entries:
                    border = _LOG_ROW_BORDER.get(entry.status, "border-slate-300")
                    with ui.row().classes(
                        f"tb-log-row w-full items-center gap-2 pl-2 border-l-4 {border}",
                    ):
                        ui.label(entry.created_at[11:19]).classes(
                            "text-[11px] text-slate-400 w-16 shrink-0",
                        )
                        ui.label(entry.account_id or "—").classes(
                            "text-[11px] text-slate-500 w-28 shrink-0 truncate",
                        )
                        ui.label(entry.event).classes("text-xs font-medium truncate")
                        if entry.extra:
                            ui.label(json.dumps(entry.extra, ensure_ascii=False)).classes(
                                "text-[11px] text-slate-400 truncate",
                            )

        await refresh_log()
        ui.timer(_LOG_POLL_SECONDS, refresh_log)
