"""NiceGUI Warming page.

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.warming`` function, and re-renders. No business logic here.

Layout:
- **Settings** — Gemini API key + model only.
- **Features** — on/off toggles for what warming accounts may do (auto-saved).
- **Channels** — add unlimited links/usernames; existing ones shown in a table.
- **Kanban** — drag accounts between *Idle* and *Warming*; dropping into the
  Warming column starts the loop, dropping back into Idle stops it.
- **Activity log** — live, colour-coded (green/amber/red) feed of warming events.

Anti-flicker: the board and the log only re-render when their content actually
changes (a content signature is compared each poll), so an idle page does no DOM
work and does not blink.

Everything below is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually, the logic it calls is unit-tested.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

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
    WarmingNotReadyError,
    add_channels,
    load_board,
    remove_channel,
    save_settings,
    start_warming,
    stop_warming,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from schemas.logs import LogEntry
    from schemas.warming import WarmingAccountState, WarmingBoardState, WarmingSettings

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
            await _render_config_cards()
            await _render_channels_card()

        initial = await load_board()
        holder: dict[str, object] = {"board": initial, "sig": _board_signature(initial)}

        @ui.refreshable
        async def render_board() -> None:
            _render_board(cast("WarmingBoardState", holder["board"]), drag, force_reload)

        async def reload(*, force: bool = False) -> None:
            board = await load_board()
            signature = _board_signature(board)
            # Skip the DOM rebuild when nothing changed — this is what stops the
            # Idle column from blinking every poll.
            if not force and signature == holder["sig"]:
                return
            holder["board"] = board
            holder["sig"] = signature
            render_board.refresh()

        def force_reload() -> asyncio.Task[None]:
            # Returned (not discarded) so the task keeps a strong reference and
            # is not garbage-collected mid-flight.
            return asyncio.create_task(reload(force=True))

        await render_board()
        ui.timer(_BOARD_POLL_SECONDS, reload)

        await _render_activity_log()


def _settings_status(model: str, *, has_key: bool) -> str:  # pragma: no cover
    return f"Model {model} · AI key {'set' if has_key else 'missing'}"


def _feature_switch(
    label: str,
    description: str,
    *,
    value: bool,
    on_toggle: Callable[[], Coroutine[Any, Any, None]],
) -> ui.switch:  # pragma: no cover
    """A labelled toggle with a sub-caption that auto-saves on change."""
    switch = ui.switch(label, value=value)
    ui.label(description).classes("text-xs text-slate-400 ml-10 -mt-2")
    switch.on_value_change(lambda _e: asyncio.create_task(on_toggle()))
    return switch


async def _render_config_cards() -> None:  # pragma: no cover
    board = await load_board()
    current = board.settings
    refs: dict[str, ui.switch] = {}

    async def persist(*, key: str | None, model: str | None, clear: bool) -> WarmingSettings:
        # Always send the full settings payload so saving one card never clobbers
        # the other: toggles pass key/model=None (preserve), the API card passes
        # the live toggle values read from ``refs``.
        return await save_settings(
            WarmingSettingsUpdate(
                inter_account_chat=bool(refs["chat"].value),
                reactions_enabled=bool(refs["reactions"].value),
                join_enabled=bool(refs["join"].value),
                gemini_api_key=key,
                gemini_model=model,
                clear_gemini_key=clear,
            ),
        )

    async def on_toggle() -> None:
        await persist(key=None, model=None, clear=False)
        ui.notify("Features updated", type="positive")

    with ui.card().classes("w-[420px] p-4 gap-2"):
        ui.label("Features").classes("text-base font-semibold")
        ui.label("What warming accounts are allowed to do — saved instantly.").classes(
            "text-xs text-slate-500",
        )
        refs["chat"] = _feature_switch(
            "Accounts chat with each other",
            "AI-written DMs between your warming accounts (needs a Gemini key).",
            value=current.inter_account_chat,
            on_toggle=on_toggle,
        )
        refs["reactions"] = _feature_switch(
            "Put reactions on posts",
            "Occasionally react to recent posts in joined channels.",
            value=current.reactions_enabled,
            on_toggle=on_toggle,
        )
        refs["join"] = _feature_switch(
            "Join new channels",
            "Let accounts join channels from your list — the riskiest action.",
            value=current.join_enabled,
            on_toggle=on_toggle,
        )

    placeholder = "key is set" if current.has_gemini_key else "paste key to enable AI chat"
    with ui.card().classes("w-[420px] p-4 gap-3"):
        ui.label("Settings").classes("text-base font-semibold")
        key_input = (
            ui.input(label="Gemini API key", placeholder=placeholder, password=True)
            .props("dense outlined clearable")
            .classes("w-full")
        )
        clear_key_checkbox = ui.checkbox("Clear stored key", value=False)
        model_input = (
            ui.input(label="Gemini model", value=current.gemini_model)
            .props("dense outlined")
            .classes("w-full")
        )
        status = ui.label(
            _settings_status(current.gemini_model, has_key=current.has_gemini_key),
        ).classes("text-xs text-slate-500")

        async def on_save() -> None:
            raw_key = (key_input.value or "").strip()
            raw_model = (model_input.value or "").strip()
            updated = await persist(
                key=raw_key or None,
                model=raw_model or None,
                clear=bool(clear_key_checkbox.value),
            )
            key_input.value = ""
            clear_key_checkbox.value = False
            model_input.value = updated.gemini_model
            status.set_text(_settings_status(updated.gemini_model, has_key=updated.has_gemini_key))
            ui.notify("Settings saved", type="positive")

        ui.button("Save settings", icon="save", on_click=on_save).props("color=primary").classes(
            "w-full",
        )


_CHANNEL_DELETE_SLOT = """
<q-td :props="props" class="text-right">
    <q-btn flat dense round icon="delete" color="grey-7"
           @click="() => $parent.$emit('delete', props.row)" />
</q-td>
"""


def _fmt_date(iso: str) -> str:  # pragma: no cover
    """Render an ISO timestamp as ``YYYY-MM-DD HH:MM`` for the table cell."""
    return iso[:16].replace("T", " ") if len(iso) >= 16 else iso  # noqa: PLR2004


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
        columns = [
            {"name": "channel", "label": "Channel", "field": "channel", "align": "left"},
            {"name": "added", "label": "Added", "field": "added", "align": "left"},
            {"name": "actions", "label": "", "field": "actions", "align": "right"},
        ]
        # Quasar diffs rows by ``row_key`` and updates in place — no flicker on
        # add/delete, unlike clearing and rebuilding a column of divs.
        table = (
            ui.table(columns=columns, rows=[], row_key="channel", pagination=0)
            .props("flat dense hide-bottom")
            .classes("w-full")
            .style("max-height: 16rem")
        )
        table.add_slot("body-cell-actions", _CHANNEL_DELETE_SLOT)

        async def refresh_list() -> None:
            board = await load_board()
            count_badge.set_text(str(board.channel_count))
            table.rows = [
                {"channel": channel.channel, "added": _fmt_date(channel.created_at)}
                for channel in board.channels.channels
            ]
            table.update()

        async def on_delete(event) -> None:  # noqa: ANN001
            await remove_channel(RemoveChannelRequest(channel=event.args["channel"]))
            await refresh_list()

        table.on("delete", on_delete)

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


def _board_signature(board: WarmingBoardState) -> tuple[object, ...]:  # pragma: no cover
    """A hashable digest of everything the board renders.

    The poll loop compares this between ticks and only rebuilds the DOM when it
    changes, so a quiet board never blinks.
    """
    cards = (*board.idle, *board.warming)
    return (
        board.channel_count,
        board.active_count,
        tuple(
            (
                card.account_id,
                card.state,
                card.health,
                card.cycles_completed,
                card.last_event,
                card.next_run_at,
                card.last_error,
                None
                if card.readiness is None
                else (card.readiness.ready, tuple(card.readiness.reasons)),
            )
            for card in cards
        ),
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
            try:
                await start_warming(StartWarmingRequest(account_id=account_id))
            except WarmingNotReadyError as exc:
                ui.notify(f"Cannot start: {'; '.join(exc.reasons)}", type="negative")
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
        if card.readiness and not card.readiness.ready:
            ui.label(f"not ready: {', '.join(card.readiness.reasons)}").classes(
                "text-[11px] text-red-600 truncate",
            )


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


async def _render_activity_log() -> None:  # pragma: no cover
    with ui.card().classes("w-full p-4 gap-2"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("bolt").classes("text-amber-500")
            ui.label("Live activity").classes("text-base font-semibold")
            ui.space()
            warming_only_switch = ui.switch("Warming only", value=True).props("dense")
        log_box = ui.column().classes("w-full gap-1 max-h-80 overflow-auto")
        seen: dict[str, object] = {"sig": None}

        async def refresh_log() -> None:
            # Pull more than _LOG_LIMIT when filtering so the warming-only view
            # is not empty after a burst of unrelated events.
            limit = _LOG_LIMIT * 4 if warming_only_switch.value else _LOG_LIMIT
            state = await load_logs_page(LogFilter(limit=limit))
            entries = state.entries
            if warming_only_switch.value:
                entries = [entry for entry in entries if entry.event.startswith("warming_")]
            entries = entries[:_LOG_LIMIT]
            # Only rebuild when the visible set of entries changed — otherwise the
            # feed re-mounts every poll and visibly blinks.
            signature = (warming_only_switch.value, tuple(entry.id for entry in entries))
            if signature == seen["sig"]:
                return
            seen["sig"] = signature
            log_box.clear()
            with log_box:
                if not entries:
                    ui.label("Waiting for activity…").classes("text-xs text-slate-400")
                for entry in entries:
                    _render_log_row(entry)

        warming_only_switch.on_value_change(lambda _e: asyncio.create_task(refresh_log()))
        await refresh_log()
        ui.timer(_LOG_POLL_SECONDS, refresh_log)
