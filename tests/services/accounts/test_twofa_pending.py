"""A pending 2FA candidate is never lost: crash, lost reply, and the settle step after them.

The owner's rule is that login via 2FA must always work. A change is staged in
``twofa_password_pending`` BEFORE the RPC, so every outcome (applied and answered,
applied and the answer lost, applied and the process died, refused) leaves a password
that a login path can try. The Telethon side is faked at ``services.accounts.twofa.execute``.
"""

from __future__ import annotations

import asyncio

import pytest

from core.db import (
    create_account,
    delete_account,
    fetch_account_twofa_pair,
    set_account_twofa_pending,
    twofa_password_candidates,
)
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import SetTwoFactorPassword, VerifyTwoFactorPassword
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import AccountActionError, remove_account_twofa, set_account_twofa
from services.accounts.twofa import authorising_twofa_password
from tests.services.accounts._twofa_support import (
    STORED,
    account_with_password,
    ok_result,
    patch_execute,
    patch_log,
    patch_lost_answer,
    patch_read,
    status,
)

_INVALID = ActionResult(
    status="failed",
    action_type="verify_twofa_password",
    account_id="x",
    error_type="TwoFactorGatewayError",
    error_message="twofa_current_password_invalid",
)


def _telegram_holding(
    monkeypatch: pytest.MonkeyPatch, live: str | None
) -> list[tuple[str, str | None]]:
    """A Telegram whose cloud password is ``live``: verify and authorised writes check it."""
    calls: list[tuple[str, str | None]] = []

    async def _execute(account_id: str, action: object) -> ActionResult:
        if isinstance(action, VerifyTwoFactorPassword):
            calls.append(("verify", action.password))
            if action.password == live:
                return ok_result(account_id, "verify_twofa_password")
            return _INVALID.model_copy(update={"account_id": account_id})
        assert isinstance(action, SetTwoFactorPassword)
        calls.append(("set", action.current_password))
        if action.current_password != live:
            return _INVALID.model_copy(update={"account_id": account_id})
        return ok_result(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _execute)
    return calls


@pytest.mark.asyncio
async def test_a_reply_lost_after_the_rpc_still_leaves_the_new_password_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline loss: Telegram applied the change, only the answer died."""
    await account_with_password("acc-lost")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    patch_lost_answer(monkeypatch)

    created = await set_account_twofa("acc-lost", AccountTwoFactorUpdateRequest())

    assert created.confirmed is False
    # BOTH are tried at login, the previous one first.
    assert await twofa_password_candidates("acc-lost") == [STORED, created.password]


@pytest.mark.asyncio
async def test_a_process_killed_after_the_rpc_leaves_the_candidate_staged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancel/crash mid-``execute`` (nothing after it runs) must not drop the candidate."""
    await account_with_password("acc-killed")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    seen: list[str] = []

    async def _die(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        assert isinstance(action, SetTwoFactorPassword)
        seen.append(action.new_password or "")
        raise asyncio.CancelledError

    monkeypatch.setattr("services.accounts.twofa.execute", _die)

    with pytest.raises(asyncio.CancelledError):
        await set_account_twofa("acc-killed", AccountTwoFactorUpdateRequest(password="the-new-one"))

    assert seen == ["the-new-one"]
    assert await fetch_account_twofa_pair("acc-killed") == (STORED, "the-new-one")


@pytest.mark.asyncio
async def test_a_fresh_set_killed_after_the_rpc_is_recoverable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-fresh-killed"))
    patch_read(monkeypatch, status())
    patch_log(monkeypatch)

    async def _die(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        raise asyncio.CancelledError

    monkeypatch.setattr("services.accounts.twofa.execute", _die)

    with pytest.raises(asyncio.CancelledError):
        await set_account_twofa(
            "acc-fresh-killed", AccountTwoFactorUpdateRequest(password="pw1-longer")
        )

    assert await twofa_password_candidates("acc-fresh-killed") == ["pw1-longer"]


@pytest.mark.asyncio
async def test_a_leftover_candidate_that_telegram_accepts_is_promoted_and_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The earlier change did land: the next write authorises with the pending password."""
    await account_with_password("acc-settle")
    await set_account_twofa_pending("acc-settle", "landed")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    calls = _telegram_holding(monkeypatch, live="landed")

    created = await set_account_twofa(
        "acc-settle", AccountTwoFactorUpdateRequest(password="third-one")
    )

    assert calls == [("verify", STORED), ("verify", "landed"), ("set", "landed")]
    assert created.confirmed is True
    assert await fetch_account_twofa_pair("acc-settle") == ("third-one", None)


@pytest.mark.asyncio
async def test_a_leftover_candidate_telegram_rejects_is_dropped_when_the_current_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await account_with_password("acc-never-applied")
    await set_account_twofa_pending("acc-never-applied", "never-applied")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    calls = _telegram_holding(monkeypatch, live=STORED)

    assert await authorising_twofa_password("acc-never-applied") == STORED
    assert calls == [("verify", STORED)]
    assert await fetch_account_twofa_pair("acc-never-applied") == (STORED, None)


@pytest.mark.asyncio
async def test_a_leftover_candidate_is_dropped_when_telegram_has_no_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash before the RPC ever left: nothing applied, so the candidate is not kept."""
    await create_account(AccountCreate(account_id="acc-before-rpc"))
    await set_account_twofa_pending("acc-before-rpc", "never-sent")
    patch_read(monkeypatch, status(has_password=False))
    patch_log(monkeypatch)
    calls = _telegram_holding(monkeypatch, live=None)

    assert await authorising_twofa_password("acc-before-rpc") is None
    assert calls == []
    assert await fetch_account_twofa_pair("acc-before-rpc") == (None, None)


@pytest.mark.asyncio
async def test_a_candidate_nothing_can_verify_stays_until_the_state_is_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verify that proves nothing (unavailable) must not discard either password."""
    await account_with_password("acc-unknown")
    await set_account_twofa_pending("acc-unknown", "maybe")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)

    async def _unavailable(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="unavailable", action_type="verify_twofa_password", account_id=account_id
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _unavailable)

    assert (
        await authorising_twofa_password("acc-unknown") is None
    )  # no guess is typed while unsettled
    assert await fetch_account_twofa_pair("acc-unknown") == (STORED, "maybe")


@pytest.mark.asyncio
async def test_without_a_pending_candidate_no_telegram_call_is_made(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await account_with_password("acc-plain")

    async def _never(*_: object) -> ActionResult:
        pytest.fail("no Telegram call expected")

    monkeypatch.setattr("services.accounts.twofa.execute", _never)
    monkeypatch.setattr("services.accounts.twofa.execute_read", _never)

    assert await authorising_twofa_password("acc-plain") == STORED


@pytest.mark.asyncio
async def test_removing_2fa_clears_the_candidate_with_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await account_with_password("acc-remove")
    await set_account_twofa_pending("acc-remove", "landed")
    patch_read(monkeypatch, status(has_password=True), status(has_password=False))
    patch_log(monkeypatch)
    _telegram_holding(monkeypatch, live="landed")

    await remove_account_twofa("acc-remove")

    assert await twofa_password_candidates("acc-remove") == []


@pytest.mark.asyncio
async def test_forgetting_clears_the_pending_candidate_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await account_with_password("acc-forget-both")
    await set_account_twofa_pending("acc-forget-both", "maybe")
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch)
    _telegram_holding(monkeypatch, live="something-else")

    await remove_account_twofa("acc-forget-both", forget_only=True)

    assert await fetch_account_twofa_pair("acc-forget-both") == (None, None)


@pytest.mark.asyncio
async def test_set_sends_nothing_when_the_row_vanished_before_the_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage that touched zero rows is not durable, so the RPC is never sent.

    The stage is an ``UPDATE ... WHERE``: an account deleted between ``require_account``
    and here changes nothing and raises nothing, and sending the RPC anyway would put a
    password on Telegram that exists nowhere on our side.
    """
    await create_account(AccountCreate(account_id="acc-vanished"))
    patch_read(monkeypatch, status())
    actions = patch_execute(monkeypatch)
    events = patch_log(monkeypatch)

    async def _delete_the_row(account_id: str, password: str | None) -> bool:
        await delete_account(account_id)
        return await set_account_twofa_pending(account_id, password)

    monkeypatch.setattr(
        "services.accounts._twofa_pending.set_account_twofa_pending", _delete_the_row
    )

    with pytest.raises(AccountActionError, match="twofa_pending_store_failed"):
        await set_account_twofa("acc-vanished", AccountTwoFactorUpdateRequest())

    assert actions == []
    assert [(level, event) for level, event, _extra in events] == [
        ("ERROR", "account_twofa_pending_failed"),
    ]


@pytest.mark.asyncio
async def test_set_sends_nothing_when_the_stage_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No durable candidate, no RPC: the secret must never exist only in a response."""
    await create_account(AccountCreate(account_id="acc-nostage"))
    patch_read(monkeypatch, status())
    actions = patch_execute(monkeypatch)
    events = patch_log(monkeypatch)

    async def _fail(account_id: str, password: str | None) -> None:  # noqa: ARG001
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts._twofa_pending.set_account_twofa_pending", _fail)

    with pytest.raises(AccountActionError, match="twofa_pending_store_failed"):
        await set_account_twofa("acc-nostage", AccountTwoFactorUpdateRequest(hint="a hint"))

    assert actions == []
    assert [(level, event) for level, event, _extra in events] == [
        ("ERROR", "account_twofa_pending_failed"),
    ]
    assert events[0][2] == {"clearing": False}


@pytest.mark.asyncio
async def test_set_still_returns_the_password_when_the_promotion_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram accepted it; a failed promotion leaves it pending and says ``stored=False``."""
    await create_account(AccountCreate(account_id="acc-nostore"))
    patch_read(monkeypatch, status())
    patch_execute(monkeypatch)
    events = patch_log(monkeypatch)

    async def _fail(account_id: str) -> None:  # noqa: ARG001
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts._twofa_pending.promote_account_twofa_pending", _fail)

    created = await set_account_twofa(
        "acc-nostore",
        AccountTwoFactorUpdateRequest(hint="a hint"),
    )

    assert created.stored is False
    assert created.password
    # Still durable as the pending candidate, so every login path can use it.
    assert await twofa_password_candidates("acc-nostore") == [created.password]
    assert [(level, event) for level, event, _extra in events] == [
        ("ERROR", "account_twofa_store_failed"),
        ("INFO", "account_twofa_set"),
    ]
    assert events[0][2] == {"has_hint": False, "clearing": False}
    assert all(created.password not in str(extra) for _l, _e, extra in events)
