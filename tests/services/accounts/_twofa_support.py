"""Shared doubles for the 2FA service tests — three modules stub the same seams.

``test_twofa.py`` (the password half), ``test_twofa_recovery.py`` (the states a
killed or lost write leaves behind) and ``test_twofa_email.py`` (the
recovery-email half) all patch ``execute`` / ``execute_read`` / ``log_event`` /
``set_account_twofa_password``, and two of those seams now live in TWO service
modules — the email flows moved to ``services.accounts._twofa_email`` when
``twofa.py`` hit the 440-line budget. So the target module is a PARAMETER here
rather than three drifting copies of the same ``monkeypatch.setattr``.

``execute_read`` is not parametrised: every live read goes through
``read_account_twofa``, which stayed in ``services.accounts.twofa``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.db import create_account, set_account_twofa_password
from core.telegram_client import UNCONFIRMED_ERROR_TYPE
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import TwoFactorStatusResult

if TYPE_CHECKING:
    import pytest

# The two service modules that own an ``execute`` / ``log_event`` global.
PASSWORD_MODULE = "services.accounts.twofa"
EMAIL_MODULE = "services.accounts._twofa_email"

STORED = "stored-password"


def ok_result(
    account_id: str, action_type: str = "set_twofa_password", **fields: Any
) -> ActionResult:
    return ActionResult(status="ok", action_type=action_type, account_id=account_id, **fields)


def status(**overrides: Any) -> TwoFactorStatusResult:
    return TwoFactorStatusResult(**overrides)


def patch_read(
    monkeypatch: pytest.MonkeyPatch,
    first: TwoFactorStatusResult,
    *later: TwoFactorStatusResult,
) -> list[str]:
    """Canned live reads; the last one answers every further read.

    ``remove_account_twofa`` reads twice — once to decide whether Telegram still has
    a password at all, once to build the response — so it needs two.
    """
    reads: list[str] = []
    queue = (first, *later)

    async def _fake(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        reads.append(account_id)
        return queue[min(len(reads) - 1, len(queue) - 1)]

    monkeypatch.setattr(f"{PASSWORD_MODULE}.execute_read", _fake)
    return reads


def patch_execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module: str = PASSWORD_MODULE,
    **result_fields: Any,
) -> list[Any]:
    """Record every dispatched action and answer ``ok`` with ``result_fields``."""
    actions: list[Any] = []

    async def _fake(account_id: str, action: Any) -> ActionResult:
        actions.append(action)
        return ok_result(account_id, action.action_type, **result_fields)

    monkeypatch.setattr(f"{module}.execute", _fake)
    return actions


def patch_lost_answer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module: str = PASSWORD_MODULE,
) -> list[Any]:
    """The write reached the wire and only the ANSWER was lost.

    Shared by every caller that has to prove its lost-answer branch, which differ in
    exactly what there was to lose — so they must stub the same seam.
    """
    actions: list[Any] = []

    async def _lost(account_id: str, action: Any) -> ActionResult:
        actions.append(action)
        return ActionResult(
            status="unavailable",
            action_type=action.action_type,
            account_id=account_id,
            error_type=UNCONFIRMED_ERROR_TYPE,
            error_message="ConnectionError()",
        )

    monkeypatch.setattr(f"{module}.execute", _lost)
    return actions


def patch_log(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module: str = PASSWORD_MODULE,
) -> list[tuple[str, str, dict[str, object]]]:
    events: list[tuple[str, str, dict[str, object]]] = []

    async def _capture(
        level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001 - mirrors log_event
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra or {}))

    monkeypatch.setattr(f"{module}.log_event", _capture)
    return events


async def account_with_password(account_id: str, password: str = STORED) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await set_account_twofa_password(account_id, password)
