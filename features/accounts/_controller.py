"""Event handlers for the accounts page.

Pulls page state (search query, selected rows, table) and routes events to
``services.accounts``. The service raises ``ValueError`` for known domain
problems (e.g. unknown account_id) and we surface those to the user via
``ui.notify`` instead of letting the exception bubble into NiceGUI's logs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nicegui import ui

from features.accounts._dialogs import (
    _check_accounts,
    _open_add_dialog,
    _open_profile_dialog,
)
from features.accounts._metrics import _refresh_metrics
from features.accounts._proxy_dialog import _open_proxy_dialog
from features.accounts._table import (
    _NOTIFY_TYPE_BY_HEALTH,
    _account_id_from_event,
    _account_status_label,
    _row_from_event,
    _to_table_row,
)
from schemas.accounts import AccountCheckRequest, AccountFilter, health_for_status
from services.accounts import check_account_session, load_accounts_table

if TYPE_CHECKING:
    from features.accounts._table_section import _TableSection


class _AccountsController:  # pragma: no cover
    """Holds page state and event handlers, keeping the page builder flat."""

    def __init__(self, section: _TableSection, selected_ids: set[str]) -> None:
        self._section = section
        self._selected_ids = selected_ids

    async def refresh(self, _event: object = None) -> None:
        section = self._section
        state = await load_accounts_table(
            AccountFilter(
                query=section.query_input.value or "",
                status=section.status_select.value,
            ),
        )
        section.table.rows = [_to_table_row(row.model_dump()) for row in state.rows]
        section.table.update()
        _refresh_metrics(section, state.summary)

    async def check_selected(self, _event: object = None) -> None:
        await _check_accounts(self._selected_ids)
        await self.refresh()

    async def check_all(self, _event: object = None) -> None:
        await _check_accounts({str(row["account_id"]) for row in self._section.table.rows})
        await self.refresh()

    async def check_one(self, event: object) -> None:
        account_id = _account_id_from_event(event)
        if not account_id:
            ui.notify("Не удалось определить ID аккаунта", type="negative")
            return
        spinner = ui.notification(
            f"Проверяем {account_id}…",
            spinner=True,
            timeout=None,
            close_button=False,
        )
        try:
            account = await check_account_session(AccountCheckRequest(account_id=account_id))
        except ValueError as exc:
            # Domain error from the service (e.g. unknown account_id) — show it
            # and stop. Without this the exception escapes into NiceGUI's event
            # loop and only appears in server logs.
            ui.notify(str(exc), type="negative")
            return
        finally:
            spinner.dismiss()
        await self.refresh()
        ui.notify(
            f"{account_id}: {_account_status_label(account.status)}",
            type=_NOTIFY_TYPE_BY_HEALTH[health_for_status(account.status)],
        )

    async def open_add(self, _event: object = None) -> None:
        await _open_add_dialog(self.refresh)

    async def open_profile(self, event: object) -> None:
        row = _row_from_event(event)
        if not row:
            ui.notify("Не удалось определить аккаунт", type="negative")
            return
        await _open_profile_dialog(row, self.refresh)

    async def open_proxy(self, event: object) -> None:
        row = _row_from_event(event)
        if not row:
            ui.notify("Не удалось определить аккаунт", type="negative")
            return
        await _open_proxy_dialog(row, self.refresh)
