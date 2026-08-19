"""Cloud-password service tests — read, set/change, remove, and the secret rule.

The gateway is monkeypatched at ``services.accounts.twofa.execute`` /
``.execute_read``; the DB is the real SQLite one the autouse fixtures build, so
the persistence half (does the column actually hold the password, is it actually
cleared) is exercised rather than mocked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import create_account, fetch_account_twofa_password, set_account_twofa_password
from core.telegram_client import (
    UNCONFIRMED_ERROR_TYPE,
    TelegramAccountNotFoundError,
    TelegramReadError,
)
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import SetTwoFactorPassword, TwoFactorStatusResult
from schemas.twofa import AccountTwoFactorUpdateRequest
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    read_account_twofa,
    remove_account_twofa,
    set_account_twofa,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_STORED = "stored-password"


def _ok(account_id: str) -> ActionResult:
    return ActionResult(status="ok", action_type="set_twofa_password", account_id=account_id)


def _status(**overrides: object) -> TwoFactorStatusResult:
    return TwoFactorStatusResult(**overrides)  # ty: ignore[invalid-argument-type]


def _patch_read(
    monkeypatch: pytest.MonkeyPatch,
    status: TwoFactorStatusResult,
    *later: TwoFactorStatusResult,
) -> list[str]:
    """Canned live reads; the last one answers every further read.

    ``remove_account_twofa`` reads twice — once to decide whether Telegram still has
    a password at all, once to build the response — so it needs two.
    """
    reads: list[str] = []
    queue = (status, *later)

    async def _fake(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        reads.append(account_id)
        return queue[min(len(reads) - 1, len(queue) - 1)]

    monkeypatch.setattr("services.accounts.twofa.execute_read", _fake)
    return reads


def _patch_execute(monkeypatch: pytest.MonkeyPatch) -> list[SetTwoFactorPassword]:
    actions: list[SetTwoFactorPassword] = []

    async def _fake(account_id: str, action: SetTwoFactorPassword) -> ActionResult:
        actions.append(action)
        return _ok(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute", _fake)
    return actions


def _patch_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, object]]]:
    events: list[tuple[str, str, dict[str, object]]] = []

    async def _capture(
        level: str,
        event: str,
        account_id: str | None = None,  # noqa: ARG001 - mirrors log_event
        extra: dict[str, object] | None = None,
    ) -> None:
        events.append((level, event, extra or {}))

    monkeypatch.setattr("services.accounts.twofa.log_event", _capture)
    return events


@pytest.mark.asyncio
async def test_read_account_twofa_returns_the_live_state_and_the_stored_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-read"))
    await set_account_twofa_password("acc-read", _STORED)
    _patch_read(monkeypatch, _status(has_password=True, hint="the usual", has_recovery=True))

    view = await read_account_twofa("acc-read")

    assert view.error is None
    assert view.status is not None
    assert (view.status.has_password, view.status.hint) == (True, "the usual")
    assert view.status.has_recovery is True
    assert view.has_stored_password is True
    assert _STORED not in view.model_dump_json()


@pytest.mark.asyncio
async def test_read_account_twofa_reports_a_refused_read_in_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal is data, not an exception — and the DB fact still comes back."""
    await create_account(AccountCreate(account_id="acc-flood"))
    await set_account_twofa_password("acc-flood", _STORED)

    async def _boom(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        reason = "FloodWait(30s)"
        raise TelegramReadError(reason)

    monkeypatch.setattr("services.accounts.twofa.execute_read", _boom)

    view = await read_account_twofa("acc-flood")

    assert view.status is None
    assert view.error == "FloodWait(30s)"
    assert view.has_stored_password is True


@pytest.mark.asyncio
async def test_read_account_twofa_translates_a_row_that_vanished_mid_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleted between the guard and the read: a 404, not a 500."""
    await create_account(AccountCreate(account_id="acc-gone"))

    async def _gone(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        raise TelegramAccountNotFoundError(account_id)

    monkeypatch.setattr("services.accounts.twofa.execute_read", _gone)

    with pytest.raises(AccountNotFoundError):
        await read_account_twofa("acc-gone")


@pytest.mark.asyncio
async def test_set_generates_a_password_when_none_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-gen"))
    _patch_read(monkeypatch, _status())
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    created = await set_account_twofa("acc-gen", AccountTwoFactorUpdateRequest())

    assert created.stored is True
    assert created.hint is None
    assert len(created.password) >= 8
    # A set (not a change): nothing was stored, so no current password is sent.
    assert [(a.current_password, a.new_password, a.hint) for a in actions] == [
        (None, created.password, ""),
    ]
    assert await fetch_account_twofa_password("acc-gen") == created.password
    assert [(level, event) for level, event, _extra in events] == [("INFO", "account_twofa_set")]
    assert events[0][2] == {
        "has_hint": False,
        "generated": True,
        "changed": False,
        "confirmed": True,
    }


@pytest.mark.asyncio
async def test_set_uses_the_operator_supplied_password_and_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-own"))
    _patch_read(monkeypatch, _status())
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    created = await set_account_twofa(
        "acc-own",
        AccountTwoFactorUpdateRequest(password="operator-chose-this", hint="mine"),
    )

    assert created.password == "operator-chose-this"
    assert created.hint == "mine"
    assert [a.hint for a in actions] == ["mine"]
    assert events[0][2] == {
        "has_hint": True,
        "generated": False,
        "changed": False,
        "confirmed": True,
    }


@pytest.mark.asyncio
async def test_set_sends_the_stored_password_as_the_current_one_on_a_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-change"))
    await set_account_twofa_password("acc-change", _STORED)
    _patch_read(monkeypatch, _status(has_password=True))
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    created = await set_account_twofa(
        "acc-change",
        AccountTwoFactorUpdateRequest(password="the-new-one"),
    )

    assert [(a.current_password, a.new_password) for a in actions] == [(_STORED, "the-new-one")]
    assert await fetch_account_twofa_password("acc-change") == "the-new-one"
    assert created.stored is True
    assert events[0][2]["changed"] is True


@pytest.mark.asyncio
async def test_set_refuses_a_change_when_no_password_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram would answer a bare invalid-password error that explains nothing."""
    await create_account(AccountCreate(account_id="acc-foreign"))
    _patch_read(monkeypatch, _status(has_password=True))
    actions = _patch_execute(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await set_account_twofa("acc-foreign", AccountTwoFactorUpdateRequest())

    assert excinfo.value.code == "twofa_password_not_stored"
    assert actions == []
    assert await fetch_account_twofa_password("acc-foreign") is None


@pytest.mark.asyncio
async def test_set_proceeds_when_the_live_read_itself_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``edit_2fa`` is the real safety net; a refused read must not block the write."""
    await create_account(AccountCreate(account_id="acc-blind"))

    async def _boom(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        reason = "unavailable: TelegramClientPoolError"
        raise TelegramReadError(reason, kind="unavailable")

    monkeypatch.setattr("services.accounts.twofa.execute_read", _boom)
    actions = _patch_execute(monkeypatch)
    _patch_log(monkeypatch)

    created = await set_account_twofa("acc-blind", AccountTwoFactorUpdateRequest())

    assert len(actions) == 1
    assert created.stored is True


@pytest.mark.asyncio
async def test_set_still_returns_the_password_when_the_db_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram already accepted it, so this response is the operator's only copy."""
    await create_account(AccountCreate(account_id="acc-nostore"))
    _patch_read(monkeypatch, _status())
    _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    async def _fail(account_id: str, password: str | None) -> None:  # noqa: ARG001
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.twofa.set_account_twofa_password", _fail)

    created = await set_account_twofa(
        "acc-nostore",
        AccountTwoFactorUpdateRequest(hint="a hint"),
    )

    assert created.stored is False
    assert created.password
    assert [(level, event) for level, event, _extra in events] == [
        ("ERROR", "account_twofa_store_failed"),
        ("INFO", "account_twofa_set"),
    ]
    # Neither the password nor the DB error prose may ride in a log extra.
    assert events[0][2] == {"has_hint": True, "clearing": False}
    assert all(created.password not in str(extra) for _l, _e, extra in events)


@pytest.mark.asyncio
async def test_set_keeps_the_password_when_only_the_answer_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request was on the wire, so Telegram may hold this password.

    Raising here would answer 503 and discard it, and if Telegram DID apply it the
    account is then behind a password nobody ever saw: the retry sees
    ``has_password=True`` with nothing stored and refuses ``twofa_password_not_stored``
    forever. So it is stored and returned, flagged ``confirmed=False``.
    """
    await create_account(AccountCreate(account_id="acc-lost"))
    _patch_read(monkeypatch, _status())
    events = _patch_log(monkeypatch)

    async def _lost(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="unavailable",
            action_type="set_twofa_password",
            account_id=account_id,
            error_type=UNCONFIRMED_ERROR_TYPE,
            error_message="ConnectionError()",
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _lost)

    created = await set_account_twofa("acc-lost", AccountTwoFactorUpdateRequest())

    assert created.confirmed is False
    assert created.stored is True
    assert await fetch_account_twofa_password("acc-lost") == created.password
    assert events[0][2]["confirmed"] is False


@pytest.mark.asyncio
async def test_set_still_raises_for_an_unavailable_that_never_reached_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of ``unavailable``: the pool never handed back a client.

    Nothing left the process, so repeating the action is free and there is no
    password to hand out — this must stay a 503 with the column untouched.
    """
    await create_account(AccountCreate(account_id="acc-nopool"))
    _patch_read(monkeypatch, _status())
    _patch_log(monkeypatch)

    async def _no_client(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="unavailable",
            action_type="set_twofa_password",
            account_id=account_id,
            error_type="TelegramClientPoolError",
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _no_client)

    with pytest.raises(AccountActionError) as excinfo:
        await set_account_twofa("acc-nopool", AccountTwoFactorUpdateRequest())

    assert excinfo.value.code == "unavailable"
    assert await fetch_account_twofa_password("acc-nopool") is None


@pytest.mark.asyncio
async def test_remove_clears_the_column_and_returns_the_fresh_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-rm"))
    await set_account_twofa_password("acc-rm", _STORED)
    _patch_read(monkeypatch, _status(has_password=True), _status())
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    view = await remove_account_twofa("acc-rm")

    # Remove is ``current_password`` alone — see the gateway module docstring.
    assert [(a.current_password, a.new_password) for a in actions] == [(_STORED, None)]
    assert await fetch_account_twofa_password("acc-rm") is None
    assert view.has_stored_password is False
    assert view.status is not None
    assert view.status.has_password is False
    assert [(level, event, extra) for level, event, extra in events] == [
        ("INFO", "account_twofa_removed", {"stale": False}),
    ]


@pytest.mark.asyncio
async def test_remove_clears_a_stored_password_telegram_no_longer_has(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2FA already off: the stored value is stale, and this is the only way to drop it.

    Reachable when the operator removed the password from their phone, or when an
    earlier removal's post-RPC clear failed. Without this branch the state is
    terminal — ``has_stored_password`` stays ``True``, so change / remove /
    attach-email are all offered and all fail (Telethon drops the current password
    when 2FA is off and answers ``twofa_not_changed``).
    """
    await create_account(AccountCreate(account_id="acc-stale"))
    await set_account_twofa_password("acc-stale", _STORED)
    _patch_read(monkeypatch, _status(has_password=False))
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    view = await remove_account_twofa("acc-stale")

    # No RPC: there is nothing left on Telegram's side to remove.
    assert actions == []
    assert await fetch_account_twofa_password("acc-stale") is None
    assert view.has_stored_password is False
    assert events[0][1] == "account_twofa_removed"
    assert events[0][2] == {"stale": True}


@pytest.mark.asyncio
async def test_remove_reports_success_when_only_the_post_rpc_clear_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram already turned 2FA off, so a locked DB must not answer 500.

    A 500 here would tell the operator the removal failed while 2FA is in fact
    gone — and the stale column is then clearable only through the branch above.
    """
    await create_account(AccountCreate(account_id="acc-rm-locked"))
    await set_account_twofa_password("acc-rm-locked", _STORED)
    _patch_read(monkeypatch, _status(has_password=True))
    _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    async def _fail(account_id: str, password: str | None) -> None:  # noqa: ARG001
        msg = "database is locked"
        raise RuntimeError(msg)

    monkeypatch.setattr("services.accounts.twofa.set_account_twofa_password", _fail)

    view = await remove_account_twofa("acc-rm-locked")

    assert view.status is not None
    assert [(level, event) for level, event, _extra in events] == [
        ("ERROR", "account_twofa_store_failed"),
        ("INFO", "account_twofa_removed"),
    ]
    assert events[0][2] == {"has_hint": False, "clearing": True}
    assert all(_STORED not in str(extra) for _l, _e, extra in events)


@pytest.mark.asyncio
async def test_remove_refuses_when_no_password_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon would drop the current password and report a call it never made."""
    await create_account(AccountCreate(account_id="acc-rm-none"))
    _patch_read(monkeypatch, _status(has_password=True))
    actions = _patch_execute(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await remove_account_twofa("acc-rm-none")

    assert excinfo.value.code == "twofa_password_not_stored"
    assert actions == []


@pytest.mark.asyncio
async def test_remove_keeps_the_stored_password_when_telegram_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clearing before the RPC would destroy the only copy that can authorise a retry."""
    await create_account(AccountCreate(account_id="acc-rm-fail"))
    await set_account_twofa_password("acc-rm-fail", _STORED)
    _patch_read(monkeypatch, _status(has_password=True))

    async def _refuse(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="failed",
            action_type="set_twofa_password",
            account_id=account_id,
            error_type="TwoFactorGatewayError",
            error_message="twofa_current_password_invalid",
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _refuse)

    with pytest.raises(AccountActionError) as excinfo:
        await remove_account_twofa("acc-rm-fail")

    assert excinfo.value.code == "twofa_current_password_invalid"
    assert await fetch_account_twofa_password("acc-rm-fail") == _STORED


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(read_account_twofa, id="read"),
        pytest.param(remove_account_twofa, id="remove"),
        pytest.param(
            lambda account_id: set_account_twofa(account_id, AccountTwoFactorUpdateRequest()),
            id="set",
        ),
    ],
)
@pytest.mark.asyncio
async def test_every_password_entry_point_raises_not_found_for_an_unknown_account(
    call: Callable[[str], Awaitable[object]],
) -> None:
    with pytest.raises(AccountNotFoundError):
        await call("acc-missing")
