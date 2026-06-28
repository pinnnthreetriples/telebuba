"""NiceGUI Warming page.

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.warming`` function, and re-renders. No business logic here. The page
is split into render modules (``_config`` / ``_channels`` / ``_board`` /
``_activity``) to keep each file small; this module wires them together.

Layout (design spec C.3 — «Прогрев аккаунтов»):
- **Header** — H1 + three counters (в прогреве / готовы / ошибки) + the master
  «Запустить/Остановить пул» toggle.
- **Two-column board** — LEFT «Готовы к прогреву» (the idle drop target),
  RIGHT «В прогреве» (the warming drop target). Dragging a card right starts
  warming; dragging it left stops it — the existing kanban wiring is unchanged.
- **Setup cards** — «Каналы прогрева», feature toggles, «Как работает прогрев».
- **Activity** — диалоги + colour-coded errors/warnings feed.

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

from core.config import settings
from features.shared import page_shell
from features.warming._activity import _render_activity_log, _render_dialogues
from features.warming._board import (
    _BoardContext,
    _card_signature,
    _render_board,
    _structural_signature,
)
from features.warming._board_styling import _BOARD_POLL_SECONDS
from features.warming._channels import _render_channels_card
from features.warming._config import _render_config_cards, _render_how_it_works
from features.warming._termlog import _toggle_expanded
from schemas.logs import LogFilter
from services.logs import load_logs_page
from services.warming import load_board

if TYPE_CHECKING:
    from schemas.warming import WarmingBoardState

__all__ = ["register_warming_page"]

# Warming-feature CSS — the spec C.3 «В прогреве» card vocabulary (pipeline
# block, 42-segment zone bar, 6-step rail, status pills/dots, tiles) expressed
# once with the exact design hex. The pure styling tables in ``_board_styling``
# and ``_pipeline`` reference these ``tbw-*`` class names. Registered via
# ``ui.add_css(shared=True)`` so the styles travel to every client at import.
_WARMING_CSS = """
.tb-dropzone { transition: background-color .2s ease, border-color .2s ease; }

/* text colours (spec token ref) */
.tbw-text-blue { color: #0066FF; }
.tbw-text-green { color: #12A150; }
.tbw-text-amber { color: #E08700; }
.tbw-text-orange { color: #E0670A; }
.tbw-text-red { color: #E5372A; }
.tbw-text-muted { color: #74726E; }
.tbw-text-faint { color: #A8A6A1; }

/* status mini-pills (statusMap + warming-card state tokens) */
.tbw-pill-green  { background: #DDF7E9; color: #12A150; }
.tbw-pill-amber  { background: #FFF0D2; color: #E08700; }
.tbw-pill-orange { background: #FFEAD6; color: #E0670A; }
.tbw-pill-red    { background: #FDE6E2; color: #E5372A; }
.tbw-pill-blue   { background: #E1ECFF; color: #0066FF; }
.tbw-pill-idle   { background: #F1EFED; color: #74726E; }

/* summary chips */
.tbw-chip-ink    { background: #F1EFED; color: #0B0B0C; }
.tbw-chip-green  { background: #DDF7E9; color: #12A150; }
.tbw-chip-amber  { background: #FFF0D2; color: #E08700; }
.tbw-chip-orange { background: #FFEAD6; color: #E0670A; }
.tbw-chip-red    { background: #FDE6E2; color: #E5372A; }

/* status-line dots */
.tbw-dot-green  { background: #12A150; }
.tbw-dot-amber  { background: #E08700; }
.tbw-dot-orange { background: #E0670A; }
.tbw-dot-red    { background: #E5372A; }
.tbw-dot-blue   { width: 8px; height: 8px; border-radius: 50%; background: #0066FF; }

/* card left stripe colours */
.tbw-stripe-green  { background: #12A150; }
.tbw-stripe-amber  { background: #E08700; }
.tbw-stripe-orange { background: #E0670A; }
.tbw-stripe-red    { background: #E5372A; }
.tbw-stripe-idle   { background: #E6E5E3; }

/* phase progress-bar fills */
.tbw-fill-blue  { background: #0066FF; }
.tbw-fill-green { background: #12A150; }
.tbw-fill-amber { background: #E08700; }

/* light-tint tiles for the detail strip */
.tbw-tile-blue   { background: #EEF4FF; }
.tbw-tile-gray   { background: #F1EFED; }
.tbw-tile-amber  { background: #FFF0D2; }
.tbw-tile-orange { background: #FFEAD6; }
.tbw-tile-red    { background: #FDE6E2; }

/* pipeline inset block */
.tbw-pipe {
    background: #f7faff; border-radius: 11px; padding: 11px 13px 9px;
    display: flex; flex-direction: column; gap: 4px;
}
.tbw-mini-label { font-size: 10.5px; color: #74726E; }
.tbw-mini-value { font-size: 11px; font-weight: 600; color: #0B0B0C; }

/* 42-segment zone bar: gradient endpoints rgb(5,117,230)→rgb(0,242,96) */
.tbw-zone { display: flex; gap: 2px; margin-top: 2px; }
.tbw-seg { flex: 1; height: 22px; border-radius: 1.5px; background: #D5DEEC; }
.tbw-seg-on {
    background: color-mix(in srgb, rgb(0,242,96) calc(var(--i) * 100%), rgb(5,117,230));
}

/* 6-step rail nodes + connectors */
.tbw-node {
    width: 14px; height: 14px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center; color: #fff;
}
.tbw-step-done   { background: #12A150; }
.tbw-step-active { width: 10px; height: 10px; background: #0066FF; }
.tbw-step-pending { width: 9px; height: 9px; background: #fff; border: 1.5px solid #D2D0CC; }
.tbw-step-error  { background: #E5372A; }
.tbw-step-flood  { background: #E08700; }
.tbw-step-quar   { background: #E0670A; }
.tbw-step-sleep  { width: 10px; height: 10px; background: #0066FF; opacity: .7; }
.tbw-step-label { font-size: 9px; line-height: 1; }
.tbw-conn { height: 2px; border-radius: 9999px; }
.tbw-conn-done    { background: #12A150; }
.tbw-conn-active  { background: #0066FF; }
.tbw-conn-pending { background: #DCE2EC; }

/* active-step strip (#EEF4FF) + off-nominal detail strip */
.tbw-active-strip {
    background: #EEF4FF; border: 1px solid #DCE7FB; border-radius: 9px;
    padding: 7px 10px; margin-top: 7px;
}
.tbw-active-text { font-size: 11.5px; font-weight: 600; color: #0066FF; }
.tbw-detail-strip {
    border-radius: 9px; padding: 7px 10px; margin-top: 7px;
    background: #EEF4FF; border: 1px solid #DCE7FB;
}
.tbw-detail-strip.flood { background: #FFFBEF; border-color: #EFE5CC; }
.tbw-detail-strip.quar  { background: #FFFBEF; border-color: #EFD79A; }
.tbw-detail-strip.error { background: #FBECEC; border-color: #F0C9C5; }
.tbw-detail-strip.sleep { background: #F6F5F2; border-color: #E6E5E3; }
.tbw-detail-tile {
    width: 28px; height: 28px; border-radius: 8px; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
}
.tbw-detail-text { font-size: 11px; line-height: 1.35; }

/* two-column board layout (spec C.3: 340px 1fr) */
.tbw-grid { display: grid; grid-template-columns: 340px 1fr; gap: 16px; align-items: start; }
@media (max-width: 1000px) { .tbw-grid { grid-template-columns: 1fr; } }
.tbw-ready-col, .tbw-warming-col {
    display: flex; flex-direction: column; gap: 12px; min-height: 240px;
}
.tbw-count-pill {
    font-size: 11px; color: #9A9893; border: 1px solid #E6E5E3;
    border-radius: 9999px; padding: 2px 8px;
}
.tbw-hero-tile {
    width: 30px; height: 30px; border-radius: 9px; background: #0066FF;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
}
.tbw-empty-zone {
    border: 1.5px dashed #DCE7FB; border-radius: 12px; padding: 28px 16px;
    text-align: center; font-size: 12px; color: #9A9893;
}
.tbw-cards-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px;
}

/* per-account card surfaces */
.tbw-card { padding: 16px 17px; cursor: grab; transition: box-shadow .15s ease; }
.tbw-card:hover { box-shadow: 0 2px 10px rgba(11,11,12,.06); }
.tbw-card-ready { background: #fff; border: 1px solid #E6E5E3; border-radius: 12px; }

/* «Как работает» numbered steps */
.tbw-how-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; margin-top: 2px;
}
@media (max-width: 560px) { .tbw-how-grid { grid-template-columns: 1fr; } }
.tbw-num {
    width: 18px; height: 18px; border-radius: 50%; background: #0066FF; color: #fff;
    font-size: 10.5px; font-weight: 600; display: flex; align-items: center;
    justify-content: center; margin-top: 1px;
}
"""

ui.add_css(_WARMING_CSS, shared=True)


def register_warming_page() -> None:  # pragma: no cover
    @ui.page("/warming", title="Telebuba — Прогрев")
    async def warming_page() -> None:
        await _render_warming_page()


async def _refresh_card_logs(ctx: _BoardContext, account_id: str) -> bool:  # pragma: no cover
    # Fetch this account's recent rows for its open log panel. Returns True only
    # when the visible set changed, so an open panel refreshes exactly when a new
    # step lands (no per-poll flicker).
    page = await load_logs_page(
        LogFilter(account_id=account_id, limit=settings.warming.card_log_limit),
    )
    new_sig = tuple(entry.id for entry in page.entries)
    if ctx.card_log_sig.get(account_id) == new_sig:
        return False
    ctx.card_log_sig[account_id] = new_sig
    ctx.card_logs[account_id] = page.entries
    return True


async def _refresh_expanded_logs(
    ctx: _BoardContext,
    board: WarmingBoardState,
) -> None:  # pragma: no cover
    for card in (*board.idle, *board.warming):
        if not ctx.card_expanded.get(card.account_id):
            continue
        if await _refresh_card_logs(ctx, card.account_id):
            ctx.card_store[card.account_id] = card
            refresher = ctx.card_refresh.get(card.account_id)
            if refresher is not None:
                refresher.refresh()


async def _toggle_card_log(ctx: _BoardContext, account_id: str) -> None:  # pragma: no cover
    # Click handler for a card's log arrow: flip the panel, fetch its logs
    # immediately on open, then refresh just that card.
    if _toggle_expanded(ctx.card_expanded, account_id):
        await _refresh_card_logs(ctx, account_id)
    refresher = ctx.card_refresh.get(account_id)
    if refresher is not None:
        refresher.refresh()


async def _render_warming_page() -> None:  # pragma: no cover
    drag: dict[str, str | None] = {"account_id": None, "source": None}

    with page_shell("/warming"):
        initial = await load_board()
        holder: dict[str, object] = {
            "board": initial,
            "struct_sig": _structural_signature(initial),
        }
        card_sigs: dict[str, tuple[object, ...]] = {
            card.account_id: _card_signature(card) for card in (*initial.idle, *initial.warming)
        }
        ctx = _BoardContext(
            drag=drag,
            refresh=lambda: force_reload(),  # noqa: PLW0108 — defer until force_reload is bound
        )

        @ui.refreshable
        def render_board() -> None:
            _render_board(cast("WarmingBoardState", holder["board"]), ctx)

        async def reload(*, force: bool = False) -> None:
            # NiceGUI maintainers' recommended pattern (discussion #2772):
            # poll into the store, then refresh only the cards whose
            # signature flipped. Structural changes (column move, count) still
            # fall back to a full rebuild because the refreshable wiring has
            # to be re-keyed under the new layout.
            board = await load_board()
            new_struct = _structural_signature(board)
            if force or new_struct != holder["struct_sig"]:
                holder["board"] = board
                holder["struct_sig"] = new_struct
                card_sigs.clear()
                for card in (*board.idle, *board.warming):
                    card_sigs[card.account_id] = _card_signature(card)
                render_board.refresh()
                return
            for card in (*board.idle, *board.warming):
                sig = _card_signature(card)
                if card_sigs.get(card.account_id) == sig:
                    continue
                card_sigs[card.account_id] = sig
                ctx.card_store[card.account_id] = card
                refresher = ctx.card_refresh.get(card.account_id)
                if refresher is not None:
                    refresher.refresh()
            await _refresh_expanded_logs(ctx, board)

        ctx.on_toggle_log = lambda account_id: _toggle_card_log(ctx, account_id)

        def force_reload() -> asyncio.Task[None]:
            # Returned (not discarded) so the task keeps a strong reference and
            # is not garbage-collected mid-flight.
            return asyncio.create_task(reload(force=True))

        render_board()
        # NiceGUI 3.x's Timer.cancel signature is ``(self, *, with_current_invocation)``;
        # passing the bound method directly trips ``client.safe_invoke`` (it sees
        # one parameter and calls ``cancel(client)``, which raises TypeError on
        # disconnect). Wrap in a no-arg lambda so safe_invoke takes the ``func()``
        # branch.
        board_timer = ui.timer(_BOARD_POLL_SECONDS, reload)
        context.client.on_disconnect(lambda: board_timer.cancel(with_current_invocation=True))

        # Setup + reference cards below the board: channels the accounts visit,
        # the always-on feature toggles, and the «Как работает» explainer.
        with ui.element("div").classes("tbw-grid w-full"):
            await _render_channels_card()
            await _render_config_cards()
        _render_how_it_works()

        await _render_dialogues()
        await _render_activity_log()
