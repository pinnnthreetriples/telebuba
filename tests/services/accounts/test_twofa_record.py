"""``record_account_twofa``: verify with Telegram, then store; never store unverified."""

from __future__ import annotations

from typing import Any

import pytest

from core.db import create_account, fetch_account_twofa_password
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import VerifyTwoFactorPassword
from services.accounts import AccountActionError, AccountNotFoundError, record_account_twofa
from tests.services.accounts._twofa_support import PASSWORD_MODULE, patch_execute, patch_log

FAKE = "fake-pass-1234567"


@pytest.mark.asyncio
async def test_a_verified_password_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="rec"))
    actions = patch_execute(monkeypatch)
    events = patch_log(monkeypatch)

    assert await record_account_twofa("rec", FAKE) is True

    assert isinstance(actions[0], VerifyTwoFactorPassword)
    assert await fetch_account_twofa_password("rec") == FAKE
    assert events == [("INFO", "account_twofa_recorded", {"stored": True})]
    assert FAKE not in str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["twofa_current_password_invalid", "twofa_not_enabled"],
)
async def test_a_refused_password_is_never_stored(
    monkeypatch: pytest.MonkeyPatch,
    code: str,
) -> None:
    await create_account(AccountCreate(account_id="rec"))

    async def _refuse(account_id: str, action: Any) -> ActionResult:
        return ActionResult(
            status="failed",
            action_type=action.action_type,
            account_id=account_id,
            error_type="TwoFactorGatewayError",
            error_message=code,
        )

    monkeypatch.setattr(f"{PASSWORD_MODULE}.execute", _refuse)

    with pytest.raises(AccountActionError) as excinfo:
        await record_account_twofa("rec", FAKE)

    assert str(excinfo.value) == code
    assert await fetch_account_twofa_password("rec") is None


@pytest.mark.asyncio
async def test_an_unreachable_telegram_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="rec"))

    async def _down(account_id: str, action: Any) -> ActionResult:
        return ActionResult(
            status="unavailable",
            action_type=action.action_type,
            account_id=account_id,
            error_type="ConnectionError",
            error_message="ConnectionError()",
        )

    monkeypatch.setattr(f"{PASSWORD_MODULE}.execute", _down)

    with pytest.raises(AccountActionError):
        await record_account_twofa("rec", FAKE)

    assert await fetch_account_twofa_password("rec") is None


@pytest.mark.asyncio
async def test_an_unknown_account_is_not_found() -> None:
    with pytest.raises(AccountNotFoundError):
        await record_account_twofa("ghost", FAKE)
