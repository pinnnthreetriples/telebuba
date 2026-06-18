"""NiceGUI Warming page.

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.warming`` function, and re-renders. No business logic here. The page
is split into render modules (``_config`` / ``_channels`` / ``_board`` /
``_activity``) to keep each file small; this module wires them together.

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

Everything here is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually, the logic it calls is unit-tested.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from nicegui import context, ui

from features.warming._activity import _render_activity_log, _render_dialogues
from features.warming._board import _board_signature, _render_board
from features.warming._board_styling import _BOARD_POLL_SECONDS
from features.warming._channels import _render_channels_card
from features.warming._config import _render_config_cards, _render_how_it_works
from services.warming import load_board

if TYPE_CHECKING:
    from schemas.warming import WarmingBoardState

__all__ = ["register_warming_page"]

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
    @ui.page("/warming", title="Telebuba — Прогрев")
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
        ui.link("Аккаунты", "/").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")
        ui.link("Прогрев", "/warming").classes("text-sm font-medium text-slate-900 no-underline")
        ui.link("Логи", "/logs").classes("text-sm text-slate-600 hover:text-slate-900 no-underline")


async def _render_warming_page() -> None:  # pragma: no cover
    ui.add_head_html(f"<style>{_WARMING_CSS}</style>")
    ui.query("body").classes("bg-slate-50 text-slate-950")
    _build_header()

    drag: dict[str, str | None] = {"account_id": None}

    with ui.column().classes("w-full max-w-[1400px] mx-auto p-4 gap-4"):
        ui.label("Прогрев аккаунтов").classes("text-xl font-semibold")
        with ui.row().classes("w-full gap-4 items-start flex-wrap"):
            await _render_config_cards()
            await _render_channels_card()

        _render_how_it_works()

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
        context.client.on_disconnect(ui.timer(_BOARD_POLL_SECONDS, reload).cancel)

        await _render_dialogues()
        await _render_activity_log()
