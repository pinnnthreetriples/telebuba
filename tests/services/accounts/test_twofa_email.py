"""Recovery-email service tests — attach, confirm, resend, cancel.

Its own module because ``tests/services/accounts/test_twofa.py`` already carries
the password half; the 700-line test-source cap
(``tests.test_architecture._TEST_FILE_MAX_LINES``) is what keeps them apart.

Every test that reaches a ``log_event`` asserts the same thing: none of the three
sensitive values — the stored password, the recovery address, the mailed code —
appears anywhere in the recorded extras.
"""

from __future__ import annotations

import pytest

from core.db import create_account, set_account_twofa_password
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import ManageTwoFactorEmail, TwoFactorStatusResult
from schemas.twofa import AccountTwoFactorEmailConfirmRequest, AccountTwoFactorEmailRequest
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    cancel_account_twofa_email,
    clear_account_twofa_email,
    confirm_account_twofa_email,
    resend_account_twofa_email,
    set_account_twofa_email,
)

_STORED = "stored-password"
_EMAIL = "recovery@example.com"
_CODE = "424242"
_SECRETS = (_STORED, _EMAIL, _CODE)


def _patch_execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    code_length: int | None = None,
) -> list[ManageTwoFactorEmail]:
    actions: list[ManageTwoFactorEmail] = []

    async def _fake(account_id: str, action: ManageTwoFactorEmail) -> ActionResult:
        actions.append(action)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            twofa_email_code_length=code_length,
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _fake)
    return actions


def _patch_read(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    async def _fake(account_id: str, action: object) -> TwoFactorStatusResult:  # noqa: ARG001
        return TwoFactorStatusResult(**overrides)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr("services.accounts.twofa.execute_read", _fake)


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


def _assert_no_secrets(events: list[tuple[str, str, dict[str, object]]]) -> None:
    rendered = str(events)
    for secret in _SECRETS:
        assert secret not in rendered, f"a log extra carried {secret!r}"


async def _account_with_password(account_id: str) -> None:
    await create_account(AccountCreate(account_id=account_id))
    await set_account_twofa_password(account_id, _STORED)


@pytest.mark.asyncio
async def test_set_email_authorises_with_the_stored_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_password("acc-mail")
    actions = _patch_execute(monkeypatch, code_length=6)
    events = _patch_log(monkeypatch)

    pending = await set_account_twofa_email(
        "acc-mail",
        AccountTwoFactorEmailRequest(email=_EMAIL),
    )

    assert pending.pending is True
    assert pending.code_length == 6
    assert [(a.mode, a.current_password, a.email, a.code) for a in actions] == [
        ("set", _STORED, _EMAIL, None),
    ]
    assert [(level, event) for level, event, _extra in events] == [
        ("INFO", "account_twofa_email_set"),
    ]
    assert events[0][2] == {"mode": "set", "pending": True}
    _assert_no_secrets(events)


@pytest.mark.asyncio
async def test_set_email_without_a_code_length_means_no_confirmation_was_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram accepted the address as already verified — the rarer branch."""
    await _account_with_password("acc-verified")
    _patch_execute(monkeypatch, code_length=None)
    events = _patch_log(monkeypatch)

    pending = await set_account_twofa_email(
        "acc-verified",
        AccountTwoFactorEmailRequest(email=_EMAIL),
    )

    assert pending.pending is False
    assert pending.code_length is None
    assert events[0][2]["pending"] is False


@pytest.mark.asyncio
async def test_set_email_refuses_before_any_rpc_when_no_password_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the current password the change cannot be authorised at all."""
    await create_account(AccountCreate(account_id="acc-nopass"))
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await set_account_twofa_email("acc-nopass", AccountTwoFactorEmailRequest(email=_EMAIL))

    assert excinfo.value.code == "twofa_password_not_stored"
    assert actions == []
    assert events == []


@pytest.mark.asyncio
async def test_confirm_email_returns_the_re_read_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_password("acc-confirm")
    actions = _patch_execute(monkeypatch)
    _patch_read(monkeypatch, has_password=True, has_recovery=True)
    events = _patch_log(monkeypatch)

    view = await confirm_account_twofa_email(
        "acc-confirm",
        AccountTwoFactorEmailConfirmRequest(code=_CODE),
    )

    assert [(a.mode, a.code) for a in actions] == [("confirm", _CODE)]
    assert view.status is not None
    assert view.status.has_recovery is True
    assert [(level, event) for level, event, _extra in events] == [
        ("INFO", "account_twofa_email_confirmed"),
    ]
    assert events[0][2] == {"mode": "confirm"}
    _assert_no_secrets(events)
    # The code is a one-time secret: it must not come back in the response either.
    assert _CODE not in view.model_dump_json()


@pytest.mark.asyncio
async def test_resend_reports_pending_without_inventing_a_code_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``account.resendPasswordEmail`` answers a bare ``Bool`` and repeats no length."""
    await _account_with_password("acc-resend")
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    pending = await resend_account_twofa_email("acc-resend")

    assert pending.pending is True
    assert pending.code_length is None
    assert [a.mode for a in actions] == ["resend"]
    assert events[0][1] == "account_twofa_email_resent"
    assert events[0][2] == {"mode": "resend"}


@pytest.mark.asyncio
async def test_cancel_leaves_the_stored_password_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The address lived only on Telegram, so there is nothing local to clear."""
    from core.db import fetch_account_twofa_password  # noqa: PLC0415 - one assertion needs it

    await _account_with_password("acc-cancel")
    actions = _patch_execute(monkeypatch)
    _patch_read(monkeypatch, has_password=True, has_recovery=False)
    events = _patch_log(monkeypatch)

    view = await cancel_account_twofa_email("acc-cancel")

    assert [a.mode for a in actions] == ["cancel"]
    assert view.has_stored_password is True
    assert await fetch_account_twofa_password("acc-cancel") == _STORED
    assert events[0][1] == "account_twofa_email_cancelled"
    assert events[0][2] == {"mode": "cancel"}


@pytest.mark.asyncio
async def test_clear_detaches_a_confirmed_address_with_the_stored_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed address comes off with ``updatePasswordSettings``, not the cancel RPC.

    So it needs the stored password to authorise itself, exactly like attaching one.
    """
    await _account_with_password("acc-clear")
    actions = _patch_execute(monkeypatch)
    _patch_read(monkeypatch, has_password=True, has_recovery=False)
    events = _patch_log(monkeypatch)

    view = await clear_account_twofa_email("acc-clear")

    assert [(a.mode, a.current_password, a.email) for a in actions] == [("clear", _STORED, None)]
    assert view.status is not None
    assert view.status.has_recovery is False
    # The cloud password is untouched: only the address was cleared.
    assert view.has_stored_password is True
    assert events[0][1] == "account_twofa_email_cleared"
    assert events[0][2] == {"mode": "clear"}
    _assert_no_secrets(events)


@pytest.mark.asyncio
async def test_clear_refuses_before_any_rpc_when_no_password_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-clear-nopass"))
    actions = _patch_execute(monkeypatch)
    events = _patch_log(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await clear_account_twofa_email("acc-clear-nopass")

    assert excinfo.value.code == "twofa_password_not_stored"
    assert actions == []
    assert events == []


@pytest.mark.asyncio
async def test_a_refused_email_write_keeps_its_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _account_with_password("acc-badcode")

    async def _refuse(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="failed",
            action_type="manage_twofa_email",
            account_id=account_id,
            error_type="TwoFactorGatewayError",
            error_message="twofa_email_code_invalid",
        )

    monkeypatch.setattr("services.accounts.twofa.execute", _refuse)
    events = _patch_log(monkeypatch)

    with pytest.raises(AccountActionError) as excinfo:
        await confirm_account_twofa_email(
            "acc-badcode",
            AccountTwoFactorEmailConfirmRequest(code=_CODE),
        )

    assert excinfo.value.code == "twofa_email_code_invalid"
    # Nothing is logged for a refusal: the executor already wrote the failure row.
    assert events == []


@pytest.mark.asyncio
async def test_every_email_entry_point_404s_on_an_unknown_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await set_account_twofa_password("acc-ghost", _STORED)  # no-op: no such row
    _patch_execute(monkeypatch)
    _patch_log(monkeypatch)

    with pytest.raises(AccountNotFoundError):
        await resend_account_twofa_email("acc-ghost")
    with pytest.raises(AccountNotFoundError):
        await cancel_account_twofa_email("acc-ghost")
    with pytest.raises(AccountNotFoundError):
        await confirm_account_twofa_email(
            "acc-ghost",
            AccountTwoFactorEmailConfirmRequest(code=_CODE),
        )
    # The two authorised writes guard the account BEFORE the stored password, so an
    # unknown one answers 404 like its siblings rather than 400 not-stored.
    with pytest.raises(AccountNotFoundError):
        await set_account_twofa_email("acc-ghost", AccountTwoFactorEmailRequest(email=_EMAIL))
    with pytest.raises(AccountNotFoundError):
        await clear_account_twofa_email("acc-ghost")
