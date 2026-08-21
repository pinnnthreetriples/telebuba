"""Recovery-email service tests — attach, confirm, resend, cancel.

Its own module because ``tests/services/accounts/test_twofa.py`` already carries
the password half; the 700-line test-source cap
(``tests.test_architecture._TEST_FILE_MAX_LINES``) is what keeps them apart.

Every test that reaches a ``log_event`` asserts the same thing: none of the three
sensitive values — the stored password, the recovery address, the mailed code —
appears anywhere in the recorded extras.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors
from telethon.tl.functions.account import GetPasswordRequest

from core.db import create_account, fetch_account_twofa_password, set_account_twofa_password
from schemas.accounts import AccountCreate
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_twofa import ManageTwoFactorEmail, TwoFactorStatusResult
from schemas.twofa import (
    AccountTwoFactorEmailConfirmRequest,
    AccountTwoFactorEmailRequest,
    AccountTwoFactorUpdateRequest,
)
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    cancel_account_twofa_email,
    clear_account_twofa_email,
    confirm_account_twofa_email,
    resend_account_twofa_email,
    set_account_twofa,
    set_account_twofa_email,
)
from services.accounts.twofa import _TWOFA_LOCKS
from tests.core.telegram_client._twofa_doubles import algo as _algo
from tests.core.telegram_client.helpers import patch_action_client
from tests.services.accounts._twofa_support import (
    EMAIL_MODULE,
    patch_log,
    patch_lost_answer,
    patch_read,
    status,
)

_STORED = "stored-password"
_EMAIL = "recovery@example.com"
_CODE = "424242"
_SECRETS = (_STORED, _EMAIL, _CODE)


def _patch_execute(
    monkeypatch: pytest.MonkeyPatch,
    *,
    code_length: int | None = None,
    unconfirmed: bool = False,
) -> list[ManageTwoFactorEmail]:
    actions: list[ManageTwoFactorEmail] = []

    async def _fake(account_id: str, action: ManageTwoFactorEmail) -> ActionResult:
        actions.append(action)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            twofa_email_code_length=code_length,
            twofa_email_unconfirmed=unconfirmed,
        )

    monkeypatch.setattr(f"{EMAIL_MODULE}.execute", _fake)
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

    monkeypatch.setattr(f"{EMAIL_MODULE}.log_event", _capture)
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
    actions = _patch_execute(monkeypatch, code_length=6, unconfirmed=True)
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

    monkeypatch.setattr(f"{EMAIL_MODULE}.execute", _refuse)
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


class _BareUnconfirmedClient:
    """Answers the SRP read, then refuses the write with a SUFFIX-LESS ``EMAIL_UNCONFIRMED``.

    Telethon maps that form to ``code_length = 0``, which the gateway reports as
    ``None`` because zero is not a length the card can use. The whole point of this
    double is that ``None`` is then the ONLY thing the service sees.
    """

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        if isinstance(request, GetPasswordRequest):
            # A REAL algorithm: ``require_fast_algo`` admits only the ``(p, g)`` pair
            # Telethon's prime check short-circuits on, so a placeholder is refused
            # before the proof and this end-to-end test would never reach the write.
            return SimpleNamespace(current_algo=_algo())
        raise errors.EmailUnconfirmedError(None)


@pytest.mark.asyncio
async def test_a_bare_email_unconfirmed_still_reports_the_address_as_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end THROUGH the real gateway, because that is how this got through.

    ``EMAIL_UNCONFIRMED`` with no ``_<N>`` suffix is Telegram saying "address
    accepted, a code has been mailed" without saying how long the code is. Deriving
    ``pending`` from the length turns exactly that answer into ``{pending: false}``,
    which :class:`AccountTwoFactorEmailPending` documents as "already verified,
    nothing asked for" — so the card drops back to the empty attach form for an
    address that IS pending. The gateway's own test asserts only the ``None``; that
    assertion is true and was never the bug.
    """
    await _account_with_password("acc-bare")
    patch_action_client(monkeypatch, _BareUnconfirmedClient())
    monkeypatch.setattr(
        "core.telegram_client._twofa_email.compute_check",
        lambda _pwd, _password: "srp-proof",
    )
    events = patch_log(monkeypatch, module=EMAIL_MODULE)

    pending = await set_account_twofa_email("acc-bare", AccountTwoFactorEmailRequest(email=_EMAIL))

    assert pending.code_length is None
    # The load-bearing assertion: the address is pending even with no length to size
    # the input with.
    assert pending.pending is True
    assert events[0][2]["pending"] is True


@pytest.mark.asyncio
async def test_an_email_write_does_not_interleave_with_a_password_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both flows READ the stored password and then send an SRP-authorised write.

    That is exactly the shape ``_TWOFA_LOCKS`` exists for, and the email half was not
    taking it: two ``updatePasswordSettings`` in flight for one account, authorised by
    two different passwords. The loser is told "wrong current password" about a
    password the dashboard itself had just replaced.

    The password half's own race test proves only that the lock exists — both of its
    writers were already inside it — so nothing covered this pair. The gauge is depth
    rather than a fixed order: either flow may win, but never both at once, and the
    email write must authorise with whatever the column held when it ran.
    """
    await _account_with_password("acc-mail-race")
    depth = {"now": 0, "max": 0}
    authorised: list[tuple[str | None, str | None]] = []

    async def _enter() -> None:
        depth["now"] += 1
        depth["max"] = max(depth["max"], depth["now"])
        # A real suspension, long enough for the other task to reach its own write if
        # nothing is keeping it out.
        await asyncio.sleep(0.01)

    async def _slow_email(account_id: str, action: ManageTwoFactorEmail) -> ActionResult:
        await _enter()
        authorised.append((action.current_password, await fetch_account_twofa_password(account_id)))
        depth["now"] -= 1
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            twofa_email_unconfirmed=True,
        )

    async def _slow_password(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        await _enter()
        depth["now"] -= 1
        return ActionResult(status="ok", action_type="set_twofa_password", account_id=account_id)

    monkeypatch.setattr(f"{EMAIL_MODULE}.execute", _slow_email)
    monkeypatch.setattr("services.accounts.twofa.execute", _slow_password)
    patch_read(monkeypatch, status(has_password=True))
    patch_log(monkeypatch, module=EMAIL_MODULE)
    patch_log(monkeypatch)

    await asyncio.gather(
        set_account_twofa_email("acc-mail-race", AccountTwoFactorEmailRequest(email=_EMAIL)),
        set_account_twofa("acc-mail-race", AccountTwoFactorUpdateRequest(password="the-new-one")),
    )

    assert depth["max"] == 1, "two authorised writes were in flight for one account"
    # And the email write authorised itself with the password that was actually in the
    # column when it ran, whichever of the two won.
    assert authorised == [(authorised[0][1], authorised[0][1])]


@pytest.mark.asyncio
async def test_a_lost_confirm_answer_is_settled_by_re_reading_the_live_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 503 here tells the operator to retry a code that can only fail from now on.

    ``account.confirmPasswordEmail`` was already on the wire, so the address may well
    be attached — and the card keeps the code and refocuses, so the operator retries
    and gets ``twofa_email_code_invalid`` or ``twofa_email_hash_expired`` for an
    address that is already confirmed. The code is single-use: no retry can ever
    succeed, and the error the operator is shown is simply false. ``set_account_twofa``
    already branches on ``UNCONFIRMED_ERROR_TYPE``; this is the same branch, settled
    by the live read instead of by persisting anything.
    """
    await _account_with_password("acc-confirm-lost")
    patch_lost_answer(monkeypatch, module=EMAIL_MODULE)
    patch_read(monkeypatch, status(has_password=True, has_recovery=True))
    events = patch_log(monkeypatch, module=EMAIL_MODULE)

    view = await confirm_account_twofa_email(
        "acc-confirm-lost",
        AccountTwoFactorEmailConfirmRequest(code=_CODE),
    )

    assert view.status is not None
    assert view.status.has_recovery is True
    assert [event for _level, event, _extra in events] == ["account_twofa_email_confirmed"]


@pytest.mark.asyncio
async def test_a_lost_confirm_answer_still_fails_when_no_recovery_email_appeared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: the live read is the evidence, and it can say no.

    ``has_recovery`` still false means the confirmation did not land, so the outage
    keeps its 503 rather than being reported as a success on the strength of nothing.
    """
    await _account_with_password("acc-confirm-lost-no")
    patch_lost_answer(monkeypatch, module=EMAIL_MODULE)
    patch_read(monkeypatch, status(has_password=True, has_recovery=False))
    events = patch_log(monkeypatch, module=EMAIL_MODULE)

    with pytest.raises(AccountActionError) as excinfo:
        await confirm_account_twofa_email(
            "acc-confirm-lost-no",
            AccountTwoFactorEmailConfirmRequest(code=_CODE),
        )

    assert excinfo.value.code == "unavailable"
    assert events == []


@pytest.mark.asyncio
async def test_a_pool_failure_on_confirm_is_still_a_plain_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OTHER half of ``unavailable``, which the ``lost`` predicate has to exclude.

    ``confirm_account_twofa_email`` treats a lost answer as possibly-confirmed and
    re-reads the live state. That is only sound when the request was already on the
    wire: ``error_type == UNCONFIRMED_ERROR_TYPE``. When the POOL never handed back a
    client nothing left the process, so re-reading and reporting success would invent
    a confirmation out of an outage — and the password half has a test for exactly
    this distinction while this half had none.
    """
    await create_account(AccountCreate(account_id="acc-nopool-mail"))
    patch_read(monkeypatch, status(has_password=True, has_recovery=True))
    patch_log(monkeypatch, module=EMAIL_MODULE)

    async def _no_client(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        return ActionResult(
            status="unavailable",
            action_type="manage_twofa_email",
            account_id=account_id,
            error_type="TelegramClientPoolError",
        )

    monkeypatch.setattr(f"{EMAIL_MODULE}.execute", _no_client)

    with pytest.raises(AccountActionError) as excinfo:
        await confirm_account_twofa_email(
            "acc-nopool-mail",
            AccountTwoFactorEmailConfirmRequest(code=_CODE),
        )

    # ``has_recovery`` is TRUE in the canned read, so a predicate that ignored the
    # error type would have reported this outage as a successful confirmation.
    assert excinfo.value.code == "unavailable"


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda account_id: set_account_twofa_email(
                account_id,
                AccountTwoFactorEmailRequest(email=_EMAIL),
            ),
            id="set-email",
        ),
        pytest.param(clear_account_twofa_email, id="clear-email"),
    ],
)
@pytest.mark.asyncio
async def test_an_unknown_account_leaves_no_lock_behind(
    call: object,
) -> None:
    """The 404 guard runs BEFORE the lock, so an unknown id cannot mint one.

    ``_TWOFA_LOCKS`` is created lazily, keyed by account id, and pruned in exactly
    one place — ``remove_account``, which needs a row. So taking the lock first meant
    five POSTs naming nonexistent ids left five ``asyncio.Lock`` objects that nothing
    would ever remove, and the route passes ``account_id`` straight through from the
    path.
    """
    with pytest.raises(AccountNotFoundError):
        await call("acc-no-such")  # ty: ignore[call-non-callable]

    assert "acc-no-such" not in _TWOFA_LOCKS
