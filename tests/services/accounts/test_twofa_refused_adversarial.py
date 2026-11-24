"""Adversarial probes of the refused-candidate mark: stale marks, races, spent attempts.

Siblings of ``test_twofa_refused.py``; each case names a way the mark could lie.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import (
    create_account,
    delete_account,
    fetch_account_twofa_pair,
    set_account_twofa_current_only,
    set_account_twofa_pending,
)
from core.repositories._accounts_twofa import (
    fetch_account_twofa_state,
    mark_account_twofa_pending_refused,
)
from core.repositories.proxies import create_and_assign_proxy
from schemas.accounts import AccountCreate
from schemas.proxy import ProxyCreateAssignment
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import SetTwoFactorPassword, VerifyTwoFactorPassword
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import (
    AccountActionError,
    read_account_twofa,
    record_account_twofa,
    remove_account_twofa,
    set_account_twofa,
)
from services.accounts import _twofa_pending as pending_module
from services.accounts.twofa import authorising_twofa_password
from tests.services.accounts._twofa_support import ok_result, patch_log, patch_read, status

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_SELLER = "seller-json-pw"
_SUCCESSOR = "successor-pw-1"
_REFUSED = "twofa_candidate_refused"


async def _staged(account_id: str) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await create_and_assign_proxy(
        ProxyCreateAssignment(
            proxy_type="socks5", host="10.0.0.9", port=1080, account_id=account_id
        ),
    )
    await set_account_twofa_pending(account_id, _SELLER)


def _refusing(
    monkeypatch: pytest.MonkeyPatch, *, during: Callable[[], Awaitable[object]] | None = None
) -> list[str]:
    """Every verify is refused; ``during`` (an async callable) runs inside the verify."""
    asked: list[str] = []

    async def _execute(account_id: str, action: object) -> ActionResult:
        if isinstance(action, VerifyTwoFactorPassword):
            asked.append(action.password)
            if during is not None:
                await during()
            return ActionResult(
                status="failed",
                action_type="verify_twofa_password",
                account_id=account_id,
                error_type="TwoFactorGatewayError",
                error_message="twofa_current_password_invalid",
            )
        assert isinstance(action, SetTwoFactorPassword)
        return ok_result(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _execute)
    return asked


@pytest.mark.asyncio
async def test_a_refusal_of_a_candidate_replaced_mid_verify_marks_nobody(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict belongs to the value that was asked, not to whatever sits there now."""
    await _staged("adv-race")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)

    async def _replace() -> None:
        await set_account_twofa_pending("adv-race", _SUCCESSOR)

    _refusing(monkeypatch, during=_replace)

    await authorising_twofa_password("adv-race")

    assert await fetch_account_twofa_state("adv-race") == (None, _SUCCESSOR, False)


@pytest.mark.asyncio
async def test_marking_needs_the_exact_pending_value_and_an_existing_account() -> None:
    await create_account(AccountCreate(account_id="adv-cas"))

    assert await mark_account_twofa_pending_refused("adv-cas", _SELLER) is False  # no pending
    assert await mark_account_twofa_pending_refused("adv-nobody", _SELLER) is False
    await set_account_twofa_pending("adv-cas", _SELLER)
    assert await mark_account_twofa_pending_refused("adv-cas", "other") is False
    assert (await fetch_account_twofa_state("adv-cas")).pending_refused is False


@pytest.mark.asyncio
async def test_a_deleted_and_re_created_account_starts_without_a_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("adv-reimport")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _refusing(monkeypatch)
    await authorising_twofa_password("adv-reimport")
    assert (await fetch_account_twofa_state("adv-reimport")).pending_refused is True

    await delete_account("adv-reimport")
    await create_account(AccountCreate(account_id="adv-reimport"))

    assert await fetch_account_twofa_state("adv-reimport") == (None, None, False)


@pytest.mark.asyncio
async def test_a_sign_in_that_set_a_current_password_keeps_the_mark_true_to_its_candidate() -> None:
    """``set_account_twofa_current_only`` leaves the candidate alone, so its mark stays."""
    await create_account(AccountCreate(account_id="adv-current-only"))
    await set_account_twofa_pending("adv-current-only", _SELLER)
    await mark_account_twofa_pending_refused("adv-current-only", _SELLER)

    await set_account_twofa_current_only("adv-current-only", "accepted-at-signin")

    assert await fetch_account_twofa_state("adv-current-only") == (
        "accepted-at-signin",
        _SELLER,
        True,
    )


@pytest.mark.asyncio
async def test_a_failed_mark_write_never_breaks_the_verdict_or_leaks_the_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("adv-markfail")
    patch_read(monkeypatch, status(has_password=True))
    events = patch_log(monkeypatch)
    _refusing(monkeypatch)

    async def _boom(_account_id: str, password: str) -> bool:
        msg = f"database is locked while writing {password}"
        raise RuntimeError(msg)

    monkeypatch.setattr(pending_module, "mark_account_twofa_pending_refused", _boom)

    assert await authorising_twofa_password("adv-markfail") is None

    assert await fetch_account_twofa_pair("adv-markfail") == (None, _SELLER)
    assert [event for _level, event, _extra in events] == ["account_twofa_pending_failed"]
    assert _SELLER not in str(events)


async def _attempt(verb: str, request: AccountTwoFactorUpdateRequest) -> object:
    if verb == "set":
        return await set_account_twofa("adv-budget", request)
    return await remove_account_twofa("adv-budget")


@pytest.mark.asyncio
async def test_a_mixed_run_of_verbs_over_a_refused_candidate_spends_one_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("adv-budget")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    asked = _refusing(monkeypatch)
    request = AccountTwoFactorUpdateRequest(password="third-pw-123")

    for attempt in ("set", "remove", "set", "remove"):
        with pytest.raises(AccountActionError) as caught:
            await _attempt(attempt, request)
        assert caught.value.code == _REFUSED

    assert asked == [_SELLER]
    assert await fetch_account_twofa_pair("adv-budget") == (None, _SELLER)


@pytest.mark.asyncio
async def test_a_wrong_typed_password_leaves_the_refused_state_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("adv-typo")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _refusing(monkeypatch)
    await authorising_twofa_password("adv-typo")

    with pytest.raises(AccountActionError):
        await record_account_twofa("adv-typo", "not-the-password")

    assert await fetch_account_twofa_state("adv-typo") == (None, _SELLER, True)


@pytest.mark.asyncio
async def test_a_later_unknown_verdict_does_not_erase_an_earlier_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("adv-sticky")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _refusing(monkeypatch)
    await authorising_twofa_password("adv-sticky")

    async def _unknown(account_id: str, _action: object) -> ActionResult:
        return ActionResult(
            status="unavailable", action_type="verify_twofa_password", account_id=account_id
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _unknown)
    pending_module._UNSETTLED_AT.clear()  # skip the recheck throttle

    await authorising_twofa_password("adv-sticky")

    assert (await fetch_account_twofa_state("adv-sticky")).pending_refused is True


@pytest.mark.asyncio
async def test_a_candidate_accepted_on_a_later_check_becomes_stored_and_unmarked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _staged("adv-late")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _refusing(monkeypatch)
    await authorising_twofa_password("adv-late")

    async def _accept(account_id: str, _action: object) -> ActionResult:
        return ok_result(account_id, "verify_twofa_password")

    monkeypatch.setattr("services.accounts.twofa.execute", _accept)
    pending_module._UNSETTLED_AT.clear()  # skip the recheck throttle

    assert await authorising_twofa_password("adv-late") == _SELLER

    view = await read_account_twofa("adv-late")
    assert (view.has_stored_password, view.has_unverified_candidate, view.candidate_refused) == (
        True,
        False,
        False,
    )
