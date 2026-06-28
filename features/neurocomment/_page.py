"""Neurocomment page renderer — setup section + work view (issue #119).

UI-thin (non-negotiable #1), fully ``pragma: no cover``. Pure-display label maps
(``_CHANNEL_STATUS_RU`` / ``_HEALTH_RU``) are the only testable surface and are
covered in ``tests/features/test_neurocomment_labels.py``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from nicegui import context, ui

from core.logging import log_event
from features.neurocomment._labels import (
    PIPELINE_STEPS,
    FleetActivity,
    PipelineStep,
    board_captcha_count,
    board_content_signature,
    board_error_count,
    campaign_options,
    campaign_status_label,
    challenge_summary,
    channel_status_colors,
    channel_status_icon,
    channel_status_label,
    counter_window_since,
    fleet_activity,
    health_label,
    live_signature,
    relative_time,
    runtime_status_text,
    solver_switch_key,
    start_block_reason,
)
from features.shared import page_shell
from services.neurocomment import (
    delete_campaign,
    list_campaigns,
    load_neurocomment_board,
    neurocomment_runtime_status,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from schemas.neurocomment import (
        CampaignList,
        NeurocommentBoard,
        NeurocommentRuntimeStatus,
    )

# Re-export the pure display helpers (moved to ``_labels`` to keep this module within
# the file-size budget) so existing importers keep importing them from ``_page``.
__all__ = [
    "PIPELINE_STEPS",
    "FleetActivity",
    "PageContext",
    "PipelineStep",
    "board_captcha_count",
    "board_content_signature",
    "board_error_count",
    "campaign_options",
    "campaign_status_label",
    "challenge_summary",
    "channel_status_colors",
    "channel_status_icon",
    "channel_status_label",
    "confirm_delete_campaign",
    "count_ready_accounts_across_active_campaigns",
    "counter_window_since",
    "fleet_activity",
    "health_label",
    "live_signature",
    "relative_time",
    "render_neurocomment_page",
    "runtime_status_text",
    "solver_switch_key",
    "start_block_reason",
]


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


def confirm_delete_campaign(campaign_id: str, name: str = "") -> None:
    """Show a confirmation dialog before deleting a campaign (spec modal 7)."""

    async def confirm() -> None:
        dialog.close()
        await delete_campaign(campaign_id)
        ui.notify("Кампания удалена", type="info")
        _reload_page()

    title = f"Удалить кампанию «{name}»?" if name else "Удалить кампанию?"
    with (
        ui.dialog() as dialog,
        ui.card().classes("p-0 w-[420px] max-w-full").style("border-radius:18px"),
        ui.column().classes("w-full gap-3").style("padding:20px"),
    ):
        ui.label(title).classes("tb-title-lg")
        ui.label(
            "Кампания, её каналы и привязка аккаунтов будут удалены. Это действие необратимо.",
        ).classes("tb-muted").style("line-height:1.5")
        with ui.row().classes("w-full justify-end gap-2").style("margin-top:4px"):
            _html_btn("Отмена", "tb-btn tb-btn-white", dialog.close)
            _html_btn("Удалить", "tb-btn tb-btn-danger", confirm)
    dialog.open()


def _html_btn(label: str, cls: str, on_click) -> ui.element:  # noqa: ANN001  # pragma: no cover
    """A design-system pill ``<button>`` (raw element so ``tb-btn*`` styles apply cleanly)."""
    btn = ui.element("button").classes(cls)
    btn.on("click", on_click)
    with btn:
        ui.html(label)
    return btn


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

    with page_shell("/neurocomment"):
        ui.html('<h1 class="tb-h1">Нейрокомментинг</h1>')

        campaign_list = await list_campaigns()
        if not campaign_list.campaigns:
            # No campaign yet → warmed overview + create form (open) + explainer.
            await render_warmed_accounts()
            await render_create_campaign(on_created=_reload_page, expanded=True)
            render_how_it_works()
            return

        # Shared Page Context for polling data
        ctx = PageContext(campaign_list.campaigns[-1].campaign_id)
        await ctx.update()

        @ui.refreshable
        async def engine_section() -> None:
            await render_engine_panel(ctx)

        @ui.refreshable
        async def setup_section() -> None:
            await render_setup(ctx.campaign_id)

        @ui.refreshable
        async def work_section() -> None:
            await render_work_view(ctx)

        def on_switch(value: str) -> None:
            ctx.campaign_id = value
            ctx.on_reload_callbacks.clear()  # prevent stale callback accumulation
            engine_section.refresh()
            setup_section.refresh()
            work_section.refresh()

        # Two-column shell — RIGHT (col 2): pipeline hero + board; LEFT (col 1):
        # warmed overview + setup + campaigns + explainer (design spec C.4).
        with ui.element("div").classes("tb-nc-grid"):
            with (
                ui.element("div")
                .classes("tb-nc-right")
                .style(
                    "display:flex;flex-direction:column;gap:16px",
                )
            ):
                await engine_section()
                await work_section()
            with (
                ui.element("div")
                .classes("tb-nc-left")
                .style(
                    "display:flex;flex-direction:column;gap:16px",
                )
            ):
                await render_warmed_accounts()
                _render_campaign_switcher(campaign_list, ctx.campaign_id, on_switch)
                await setup_section()
                await render_create_campaign(on_created=_reload_page, expanded=False)
                render_how_it_works()

        # Page-level timer coordinates all polling
        timer = ui.timer(4.0, ctx.update)
        context.client.on_disconnect(lambda: timer.cancel(with_current_invocation=True))


def _render_campaign_switcher(
    campaign_list: CampaignList,
    current: str,
    on_switch,  # noqa: ANN001
) -> None:  # pragma: no cover
    """Campaign picker + delete, framed as a compact «Кампании» card (design spec C.4)."""
    with ui.element("div").classes("tb-card").style("padding:13px 14px"):
        with ui.row().classes("w-full items-center gap-2").style("margin-bottom:8px"):
            ui.html(
                '<div style="width:28px;height:28px;border-radius:8px;background:#EEF4FF;'
                'color:#0066FF;display:flex;align-items:center;justify-content:center">'
                '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
                ' stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M3 5h18M3 12h18M3 19h18"/></svg></div>',
            )
            ui.label("Кампании").classes("tb-title")
        with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
            switcher = (
                ui.select(
                    campaign_options(campaign_list),
                    value=current,
                    on_change=lambda e: on_switch(e.value),
                )
                .props("dense outlined options-dense")
                .classes("flex-1")
            )
            names = {c.campaign_id: c.name for c in campaign_list.campaigns}
            del_btn = ui.button(
                icon="delete_outline",
                on_click=lambda: confirm_delete_campaign(
                    switcher.value, names.get(switcher.value, "")
                ),
            )
            del_btn.props("flat round dense").classes("tb-icon-btn")
            del_btn.tooltip("Удалить кампанию")


def _reload_page() -> None:  # pragma: no cover
    ui.navigate.to("/neurocomment")
