"""Second audit of the pending-candidate flow: stale SRP, lost writes, unsettled guesses.

Hard requirement: Telegram's real password is always in one of the two columns.
"""

from __future__ import annotations

import pytest

from core.db import create_account, fetch_account_twofa_pair, set_account_twofa_pending
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import SetTwoFactorPassword, VerifyTwoFactorPassword
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import AccountActionError, set_account_twofa
from services.accounts.twofa import (
    authorising_twofa_password,
    authorising_twofa_password_unlocked,
)
from tests.services.accounts._twofa_support import (
    STORED,
    account_with_password,
    ok_result,
    patch_log,
    patch_read,
    status,
)

_MODULE = "services.accounts.twofa"


def _answer(account_id: str, action_type: str, code: str | None, kind: str) -> ActionResult:
    return ActionResult(
        status="failed",
        action_type=action_type,
        account_id=account_id,
        error_type=kind,
        error_message=code,
    )


def _verify_says(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    async def _execute(account_id: str, action: object) -> ActionResult:
        assert isinstance(action, VerifyTwoFactorPassword)
        return _answer(account_id, "verify_twofa_password", code, "TwoFactorGatewayError")

    monkeypatch.setattr(f"{_MODULE}.execute", _execute)


async def _pending_only(account_id: str, pending: str = "only-copy") -> None:
    await create_account(AccountCreate(account_id=account_id))
    await set_account_twofa_pending(account_id, pending)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["twofa_srp_stale", "twofa_current_password_invalid"])
async def test_a_pending_only_copy_is_never_dropped_on_a_refusal(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    await _pending_only("acc-only-copy")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_says(monkeypatch, code)

    assert await authorising_twofa_password("acc-only-copy") is None

    assert await fetch_account_twofa_pair("acc-only-copy") == (None, "only-copy")


@pytest.mark.asyncio
async def test_an_unsettled_candidate_gives_no_password_to_type_but_email_keeps_the_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await account_with_password("acc-guess")
    await set_account_twofa_pending("acc-guess", "maybe")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_says(monkeypatch, "twofa_srp_stale")

    assert await authorising_twofa_password("acc-guess") is None
    assert await authorising_twofa_password_unlocked("acc-guess") == STORED
    assert await fetch_account_twofa_pair("acc-guess") == (STORED, "maybe")


async def _set_failing(
    monkeypatch: pytest.MonkeyPatch, account_id: str, code: str | None, kind: str
) -> None:
    patch_read(monkeypatch, status())
    patch_log(monkeypatch)

    async def _execute(account_id: str, action: object) -> ActionResult:
        assert isinstance(action, SetTwoFactorPassword)
        return _answer(account_id, "set_twofa_password", code, kind)

    monkeypatch.setattr(f"{_MODULE}.execute", _execute)
    with pytest.raises(AccountActionError):
        await set_account_twofa(account_id, AccountTwoFactorUpdateRequest(password="new-one-pw"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "kind"),
    [(None, "ProxyConnectionError"), (None, "AuthKeyNotFound"), (None, "OSError")],
)
async def test_an_unanswered_failure_keeps_the_candidate(
    monkeypatch: pytest.MonkeyPatch, code: str | None, kind: str
) -> None:
    """Telethon can fail a request Telegram already applied: only an ANSWER proves not."""
    await create_account(AccountCreate(account_id="acc-unanswered"))

    await _set_failing(monkeypatch, "acc-unanswered", code, kind)

    assert await fetch_account_twofa_pair("acc-unanswered") == (None, "new-one-pw")


@pytest.mark.asyncio
async def test_an_answered_refusal_restores_the_previous_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-answered"))

    await _set_failing(
        monkeypatch, "acc-answered", "twofa_settings_invalid", "TwoFactorGatewayError"
    )

    assert await fetch_account_twofa_pair("acc-answered") == (None, None)


@pytest.mark.asyncio
async def test_ok_results_still_promote(monkeypatch: pytest.MonkeyPatch) -> None:
    await create_account(AccountCreate(account_id="acc-ok"))
    patch_read(monkeypatch, status())
    patch_log(monkeypatch)

    async def _execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ok_result(account_id)

    monkeypatch.setattr(f"{_MODULE}.execute", _execute)
    await set_account_twofa("acc-ok", AccountTwoFactorUpdateRequest(password="new-one-pw"))

    assert await fetch_account_twofa_pair("acc-ok") == ("new-one-pw", None)
