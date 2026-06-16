"""Channels card — add/list/remove the channels warming accounts visit.

UI-thin per non-negotiable #1; excluded from coverage. Logic lives in
``services.warming``.
"""

from __future__ import annotations

from nicegui import ui

from schemas.warming import AddChannelsRequest, RemoveChannelRequest
from services.warming import add_channels, load_board, remove_channel

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
            ui.label("Каналы").classes("text-base font-semibold")
            count_badge = ui.label("0").classes(
                "text-xs px-2 py-1 rounded bg-slate-100 text-slate-700",
            )
        channels_input = (
            ui.textarea(
                label="Добавить каналы",
                placeholder="@channel, https://t.me/channel — по одному в строке или через запятую",
            )
            .props("dense outlined autogrow")
            .classes("w-full")
        )
        columns = [
            {"name": "channel", "label": "Канал", "field": "channel", "align": "left"},
            {"name": "added", "label": "Добавлен", "field": "added", "align": "left"},
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
                ui.notify("Введите хотя бы один канал", type="warning")
                return
            result = await add_channels(AddChannelsRequest(raw=raw))
            channels_input.value = ""
            await refresh_list()
            ui.notify(f"Всего каналов: {len(result.channels)}", type="positive")

        ui.button("Добавить каналы", icon="add", on_click=on_add).props("color=primary").classes(
            "w-full",
        )
        await refresh_list()
