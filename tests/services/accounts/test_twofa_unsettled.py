"""An unsettled pending 2FA candidate is never overwritten, and settling is bounded.

Hard requirement: Telegram's real password is always in one of the two columns. A write
that stages over a pending candidate nobody has proven applied-or-not could lose it.
"""

from __future__ import annotations

import asyncio

import pytest

from core.config import settings
from core.db import create_account, fetch_account_twofa_pair, set_account_twofa_pending
from core.telegram_client import TelegramReadError
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import SetTwoFactorPassword, VerifyTwoFactorPassword
from schemas.twofa import AccountTwoFactorEmailRequest, AccountTwoFactorUpdateRequest
from services.accounts import (
    AccountActionError,
    remove_account_twofa,
    set_account_twofa,
    set_account_twofa_email,
)
from services.accounts.twofa import authorising_twofa_password, twofa_lock
from tests.services.accounts._twofa_support import (
    EMAIL_MODULE,
    STORED,
    account_with_password,
    ok_result,
    patch_execute,
    patch_log,
    patch_read,
    status,
)

_UNSETTLED = "twofa_pending_unsettled"


def _verify_answers(
    monkeypatch: pytest.MonkeyPatch, answer: str, *, module: str = "services.accounts.twofa"
) -> list[tuple[str, str | None]]:
    """Every verify answers ``answer`` (``ok`` / ``refused`` / ``unavailable``)."""
    calls: list[tuple[str, str | None]] = []

    async def _execute(account_id: str, action: object) -> ActionResult:
        if isinstance(action, VerifyTwoFactorPassword):
            calls.append(("verify", action.password))
            if answer == "ok":
                return ok_result(account_id, "verify_twofa_password")
            if answer == "refused":
                return ActionResult(
                    status="failed",
                    action_type="verify_twofa_password",
                    account_id=account_id,
                    error_type="TwoFactorGatewayError",
                    error_message="twofa_current_password_invalid",
                )
            return ActionResult(
                status="unavailable", action_type="verify_twofa_password", account_id=account_id
            )
        assert isinstance(action, SetTwoFactorPassword)
        calls.append(("set", action.current_password))
        return ok_result(account_id)

    monkeypatch.setattr(f"{module}.execute", _execute)
    return calls


async def _stage(account_id: str, pending: str = "maybe-applied") -> None:
    await account_with_password(account_id)
    await set_account_twofa_pending(account_id, pending)


@pytest.mark.asyncio
async def test_a_set_never_stages_over_a_pending_candidate_when_the_live_read_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline loss: B pending (answer lost), the read is down, C must not replace B."""
    await _stage("acc-read-down")
    patch_log(monkeypatch)

    async def _down(account_id: str, action: object) -> None:  # noqa: ARG001
        msg = "unavailable: ConnectionError"
        raise TelegramReadError(msg)

    monkeypatch.setattr("services.accounts.twofa.execute_read", _down)
    actions = patch_execute(monkeypatch)

    with pytest.raises(AccountActionError, match=_UNSETTLED):
        await set_account_twofa("acc-read-down", AccountTwoFactorUpdateRequest(password="third-pw"))

    assert actions == []
    assert await fetch_account_twofa_pair("acc-read-down") == (STORED, "maybe-applied")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["unavailable", "refused"])
async def test_a_set_refuses_while_nothing_proves_which_candidate_is_in_force(
    monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    await _stage("acc-unproven")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    calls = _verify_answers(monkeypatch, answer)

    with pytest.raises(AccountActionError, match=_UNSETTLED):
        await set_account_twofa("acc-unproven", AccountTwoFactorUpdateRequest(password="third-pw"))

    assert all(kind == "verify" for kind, _ in calls)
    assert await fetch_account_twofa_pair("acc-unproven") == (STORED, "maybe-applied")


@pytest.mark.asyncio
async def test_a_removal_refuses_over_an_unsettled_candidate_but_forgetting_does_not_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _stage("acc-remove-unsettled")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    calls = _verify_answers(monkeypatch, "unavailable")

    with pytest.raises(AccountActionError, match=_UNSETTLED):
        await remove_account_twofa("acc-remove-unsettled")
    assert await fetch_account_twofa_pair("acc-remove-unsettled") == (STORED, "maybe-applied")

    calls.clear()
    await remove_account_twofa("acc-remove-unsettled", forget_only=True)

    assert calls == []  # forgetting spends no checkPassword attempt
    assert await fetch_account_twofa_pair("acc-remove-unsettled") == (None, None)


@pytest.mark.asyncio
async def test_an_unsettled_pair_is_not_put_to_telegram_again_within_the_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _stage("acc-burn")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    calls = _verify_answers(monkeypatch, "refused")

    assert await authorising_twofa_password("acc-burn") is None
    asked = len(calls)
    assert asked >= 1
    for _ in range(3):
        assert await authorising_twofa_password("acc-burn") is None
    assert len(calls) == asked

    # A different pair is a new question, and a zero interval turns the limit off.
    monkeypatch.setattr(settings.telegram, "settle_recheck_seconds", 0.0)
    await authorising_twofa_password("acc-burn")
    assert len(calls) == asked * 2


@pytest.mark.asyncio
async def test_settling_waits_for_a_concurrent_write_holding_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Settlement can clear a candidate, so it must not interleave with a staging write."""
    await _stage("acc-locked")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _verify_answers(monkeypatch, "ok")

    async with twofa_lock("acc-locked"):
        waiting = asyncio.ensure_future(authorising_twofa_password("acc-locked"))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        assert await fetch_account_twofa_pair("acc-locked") == (STORED, "maybe-applied")
    assert await asyncio.wait_for(waiting, 2) == STORED
    assert await fetch_account_twofa_pair("acc-locked") == (STORED, None)


@pytest.mark.asyncio
async def test_nothing_pending_neither_takes_the_lock_nor_asks_telegram() -> None:
    await account_with_password("acc-quiet")
    async with twofa_lock("acc-quiet"):
        assert await asyncio.wait_for(authorising_twofa_password("acc-quiet"), 1) == STORED


@pytest.mark.asyncio
async def test_the_email_flow_settles_inside_its_own_lock_without_deadlocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _stage("acc-email")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    patch_log(monkeypatch, module=EMAIL_MODULE)
    _verify_answers(monkeypatch, "ok")
    actions = patch_execute(monkeypatch, module=EMAIL_MODULE, twofa_email_unconfirmed=True)

    await asyncio.wait_for(
        set_account_twofa_email("acc-email", AccountTwoFactorEmailRequest(email="a@example.com")),
        2,
    )

    assert [action.current_password for action in actions] == [STORED]


@pytest.mark.asyncio
async def test_an_unknown_account_pair_stays_untouched_by_the_guard() -> None:
    await create_account(AccountCreate(account_id="acc-empty"))
    assert await authorising_twofa_password("acc-empty") is None
