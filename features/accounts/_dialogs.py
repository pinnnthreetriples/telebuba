"""Accounts dialogs — facade re-exporting the per-dialog modules.

The actual UI lives in ``_add_dialog`` and ``_profile_dialog`` (split out so
each file stays under the aislop length cap). This module keeps the single
``check accounts`` action used by the page controller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from core.logging import log_event
from features.accounts._add_dialog import _open_add_dialog
from features.accounts._profile_dialog import _open_profile_dialog
from schemas.accounts import AccountCheckRequest
from services.accounts import check_account_session, remove_account

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    "_check_accounts",
    "_open_add_dialog",
    "_open_delete_dialog",
    "_open_profile_dialog",
]


async def _check_accounts(account_ids: set[str]) -> None:  # pragma: no cover
    if not account_ids:
        ui.notify("Аккаунты не выбраны", type="warning")
        return
    ids = sorted(account_ids)
    total = len(ids)
    progress = ui.notification(
        f"Проверка 0 / {total}…",
        spinner=True,
        timeout=None,
        close_button=False,
    )
    failed = 0
    try:
        for index, account_id in enumerate(ids, start=1):
            progress.message = f"Проверка {index} / {total}: {account_id}"
            try:
                await check_account_session(AccountCheckRequest(account_id=account_id))
            except Exception as exc:  # noqa: BLE001 — keep iterating, surface tally at the end
                failed += 1
                await log_event(
                    "ERROR",
                    "account_check_bulk_item_failed",
                    account_id=account_id,
                    extra={"error_type": type(exc).__name__, "error": str(exc)},
                )
    finally:
        progress.dismiss()
    if failed:
        ui.notify(
            f"Проверено {total - failed} / {total}, ошибок: {failed}",
            type="warning",
            timeout=6000,
        )
    else:
        ui.notify(f"Проверка сессий завершена ({total})", type="positive")


async def _open_delete_dialog(  # pragma: no cover
    row: dict[str, object],
    refresh: Callable[[], Awaitable[None]],
) -> None:
    account_id = str(row["account_id"])
    label = str(row.get("label") or account_id)
    with ui.dialog() as dialog, ui.column().classes("bg-white p-4 gap-3 w-[420px] max-w-full"):
        ui.label("Удалить аккаунт?").classes("text-base font-semibold")
        ui.label(
            f"Аккаунт «{label}» ({account_id}) будет удалён вместе с прогревом, "
            "прокси и историей. Действие необратимо.",
        ).classes("text-sm text-slate-700")

        async def confirm() -> None:
            spinner = ui.notification(
                f"Удаляем {account_id}…",
                spinner=True,
                timeout=None,
                close_button=False,
            )
            try:
                await remove_account(account_id)
            except Exception as exc:  # noqa: BLE001 — surface failure instead of silent close
                await log_event(
                    "ERROR",
                    "account_delete_failed",
                    account_id=account_id,
                    extra={"error_type": type(exc).__name__, "error": str(exc)},
                )
                ui.notify(f"Не удалось удалить {account_id}", type="negative")
                return
            finally:
                spinner.dismiss()
            dialog.close()
            ui.notify(f"Аккаунт {account_id} удалён", type="positive")
            await refresh()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Отмена", color="grey-7", on_click=dialog.close).props("flat")
            ui.button("Удалить", color="negative", on_click=confirm)
    dialog.open()
