"""Trust hover + per-card promotion controls extracted from ``_board``.

Split out so :mod:`features.warming._board` stays under the aislop file-size
cap. UI-thin per non-negotiable #1; every function is excluded from coverage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from core.config import settings
from features.warming._board_checks import _check_states
from features.warming._board_styling import _TRUST_LABEL_RU

if TYPE_CHECKING:
    from schemas.warming import WarmingAccountState

_CHECK_DOT_TOOLTIP = {"ok": "bg-emerald-400", "warn": "bg-amber-400", "fail": "bg-red-400"}

_SPAM_BREAKDOWN_ROW: dict[str | None, tuple[str, str]] = {
    "clean": ("ok", "@SpamBot: чисто"),
    "limited": ("fail", "@SpamBot: ограничен"),
}


def render_trust_breakdown(card: WarmingAccountState) -> None:  # pragma: no cover
    """Hover panel listing each component that feeds into the trust score."""
    band = card.trust_band or ""
    band_label = _TRUST_LABEL_RU.get(band, f"Trust {card.trust_score}")
    spam_status, spam_tooltip = _SPAM_BREAKDOWN_ROW.get(
        card.spam_status, ("warn", "@SpamBot ещё не запрашивался")
    )
    if card.spam_status == "limited" and card.spam_detail:
        spam_tooltip = card.spam_detail
    rows = [*_check_states(card), ("спам", spam_status, spam_tooltip)]
    with ui.column().classes("gap-1 px-3 py-2 min-w-[220px]"):
        with ui.row().classes("items-baseline gap-2"):
            ui.label(f"Trust {card.trust_score}/100").classes("text-sm font-semibold")
            ui.label(band_label).classes("text-[10px] opacity-70")
        for label, status, tooltip in rows:
            with ui.row().classes("items-center gap-2 w-full"):
                dot_cls = _CHECK_DOT_TOOLTIP.get(status, "bg-slate-400")
                ui.element("div").classes(f"w-2 h-2 rounded-full shrink-0 {dot_cls}")
                ui.label(label.capitalize()).classes("text-[11px]")
                ui.element("div").classes("flex-1")
                ui.label(tooltip).classes("text-[10px] opacity-70 text-right")


def render_promotion_block(refresh: object, card: WarmingAccountState) -> None:  # pragma: no cover
    """Single "переместить в нейрокомментинг / завершить прогрев" button per card.

    Promoting an account stops its warming loop and flips ``promoted_to_nc`` so it
    appears in the neurocomment warmed-list — accounts no longer auto-graduate on
    crossing ``warmed_min_days`` alone. ``settings.neurocomment.warmed_min_days`` is
    enforced as a sanity floor; the disabled-state tooltip names what's missing.
    Both promote and undo go through a confirmation dialog so a misclick can't
    move an account.

    ``refresh`` is the parent board's ``ctx.refresh`` callable — typed as ``object``
    here to keep this module from depending on the ``_BoardContext`` dataclass.
    """
    from services.warming import promote_to_neurocomment, unmark_neurocomment  # noqa: PLC0415

    nc_min_days = settings.neurocomment.warmed_min_days
    if card.promoted_to_nc:
        _render_promoted_row(refresh, card, unmark_neurocomment)
        return

    enabled = card.warming_days is not None and card.warming_days >= max(1, nc_min_days)
    label = "Переместить в нейрокомментинг" if card.state == "idle" else "Завершить прогрев"

    async def on_click() -> None:
        await _open_promote_dialog(refresh, card, promote_to_neurocomment)

    btn = (
        ui.button(label, icon="arrow_forward", on_click=on_click)
        .props("unelevated no-caps" if enabled else "unelevated no-caps disable")
        .classes(f"tb-btn w-full {'tb-btn-dark' if enabled else 'tb-btn-disabled'}")
    )
    if enabled:
        btn.tooltip("Останавливает прогрев и добавляет аккаунт в нейрокомментинг")
    elif card.warming_days is None:
        btn.tooltip("Аккаунт ещё не прогревался")
    else:
        btn.tooltip(
            f"Доступно после {max(1, nc_min_days)} дней прогрева (сейчас: {card.warming_days} д)",
        )


def _render_promoted_row(  # pragma: no cover
    refresh: object,
    card: WarmingAccountState,
    unmark: object,
) -> None:
    """The post-promotion row: green badge on the left, "Вернуть в прогрев" on the right."""
    with ui.row().classes("w-full items-center gap-2"):
        ui.label("В нейрокомментинге").classes(
            "tb-badge tbw-pill-green text-[10.5px] font-semibold",
        )
        ui.element("div").classes("flex-1")

        async def on_undo() -> None:
            await _open_undo_dialog(refresh, card, unmark)

        ui.button("Вернуть в прогрев", icon="undo", on_click=on_undo).props(
            "flat dense no-caps color=grey-7",
        ).classes("text-[10px]")


async def _open_promote_dialog(  # pragma: no cover
    refresh: object,
    card: WarmingAccountState,
    promote: object,
) -> None:
    """Confirmation dialog for promote → calls the service + refreshes the board."""
    with (
        ui.dialog() as dialog,
        ui.element("div").classes("tb-card w-[420px] max-w-full flex flex-col gap-3"),
    ):
        ui.label("Переместить аккаунт в нейрокомментинг?").classes("tb-title-lg")
        ui.label(
            "Прогрев остановится; аккаунт появится в списке готовых к комментированию.",
        ).classes("tb-label")

        async def confirm() -> None:
            dialog.close()
            await promote(card.account_id)  # ty: ignore[call-non-callable]
            ui.notify("Аккаунт перемещён в нейрокомментинг", type="positive")
            refresh()  # ty: ignore[call-non-callable]

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Отмена", on_click=dialog.close).props("unelevated no-caps").classes(
                "tb-btn tb-btn-white",
            )
            ui.button("Подтвердить", on_click=confirm).props("unelevated no-caps").classes(
                "tb-btn tb-btn-primary",
            )
    dialog.open()


async def _open_undo_dialog(  # pragma: no cover
    refresh: object,
    card: WarmingAccountState,
    unmark: object,
) -> None:
    """Confirmation dialog for un-promote → calls the service + refreshes the board."""
    with (
        ui.dialog() as dialog,
        ui.element("div").classes("tb-card w-[420px] max-w-full flex flex-col gap-3"),
    ):
        ui.label("Вернуть аккаунт в прогрев?").classes("tb-title-lg")
        ui.label(
            "Аккаунт исчезнет из списка готовых к комментированию и снова появится в простое.",
        ).classes("tb-label")

        async def confirm() -> None:
            dialog.close()
            await unmark(card.account_id)  # ty: ignore[call-non-callable]
            ui.notify("Аккаунт возвращён в прогрев", type="info")
            refresh()  # ty: ignore[call-non-callable]

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Отмена", on_click=dialog.close).props("unelevated no-caps").classes(
                "tb-btn tb-btn-white",
            )
            ui.button("Подтвердить", on_click=confirm).props("unelevated no-caps").classes(
                "tb-btn tb-btn-primary",
            )
    dialog.open()
