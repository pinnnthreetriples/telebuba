"""Accounts dialogs — facade re-exporting the per-dialog modules.

The actual UI lives in ``_add_dialog`` and ``_profile_dialog`` (split out so
each file stays under the aislop length cap). This module keeps the single
``check accounts`` action used by the page controller.
"""

from __future__ import annotations

from nicegui import ui

from features.accounts._add_dialog import _open_add_dialog
from features.accounts._profile_dialog import _open_profile_dialog
from schemas.accounts import AccountCheckRequest
from services.accounts import check_account_session

__all__ = ["_check_accounts", "_open_add_dialog", "_open_profile_dialog"]


async def _check_accounts(account_ids: set[str]) -> None:  # pragma: no cover
    if not account_ids:
        ui.notify("Аккаунты не выбраны", type="warning")
        return
    for account_id in sorted(account_ids):
        await check_account_session(AccountCheckRequest(account_id=account_id))
    ui.notify("Проверка сессий завершена", type="positive")
