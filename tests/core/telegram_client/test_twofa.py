"""Cloud-password dispatch tests — ``account.getPassword`` / ``client.edit_2fa``.

The password is a credential, so one of these tests is not about behaviour at
all: :func:`test_no_dispatched_value_or_error_message_carries_the_password` walks
everything the module produced (the ``edit_2fa`` kwargs it recorded excepted) and
asserts the secret is in none of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from telethon import errors
from telethon.tl.functions.account import GetPasswordRequest

from core.telegram_client import execute, execute_read
from core.telegram_client._twofa import TwoFactorGatewayError, dispatch_set_twofa_password
from schemas.telegram_actions import GetTwoFactorStatus, SetTwoFactorPassword
from schemas.telegram_actions_twofa import TwoFactorStatusResult
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client

if TYPE_CHECKING:
    from collections.abc import Mapping

_PASSWORD = "s3cret-passphrase"


class _Password:
    """Just the ``account.Password`` attributes the dispatcher reads.

    Deliberately not a ``MagicMock``: a mock answers every attribute with another
    mock, which is exactly the shape ``_flag`` / ``_text`` must reject, so a mock
    would hide the coercion instead of exercising it.
    """

    def __init__(self, **fields: object) -> None:
        for name, value in fields.items():
            setattr(self, name, value)


class _PasswordClient:
    """Answers ``GetPasswordRequest`` with one canned reply; records every request."""

    def __init__(self, reply: object) -> None:
        self._reply = reply
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        assert isinstance(request, GetPasswordRequest)
        return self._reply


class _EditClient:
    """Records the ``edit_2fa`` kwargs, returns a canned result or raises."""

    def __init__(self, *, result: bool = True, error: Exception | None = None) -> None:
        self._result = result
        self._error = error
        self.calls: list[Mapping[str, Any]] = []
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:  # pragma: no cover - no raw RPC here
        self.requests.append(request)
        return None

    async def edit_2fa(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


@pytest.mark.asyncio
async def test_get_twofa_status_maps_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    client = _PasswordClient(
        _Password(
            has_password=True,
            hint="the usual one",
            has_recovery=True,
            pending_reset_date=reset_at,
        ),
    )
    patch_read_client(monkeypatch, client)

    result = await execute_read("acc-2fa", GetTwoFactorStatus())

    assert isinstance(result, TwoFactorStatusResult)
    assert result.has_password is True
    assert result.hint == "the usual one"
    assert result.has_recovery is True
    assert result.pending_reset_date == reset_at.isoformat()
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_get_twofa_status_absent_fields_degrade_to_no_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every field in ``account.Password`` is an optional TL flag.

    An account with 2FA off answers with the flags simply missing, so "absent"
    must read as "no password" rather than raising or guessing.
    """
    patch_read_client(monkeypatch, _PasswordClient(_Password()))

    result = await execute_read("acc-2fa", GetTwoFactorStatus())

    assert isinstance(result, TwoFactorStatusResult)
    assert result.has_password is False
    assert result.has_recovery is False
    assert result.hint is None
    assert result.pending_reset_date is None


@pytest.mark.asyncio
async def test_get_twofa_status_ignores_values_of_the_wrong_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A truthy non-``True`` flag is not a verdict, and a non-date is not a date."""
    patch_read_client(
        monkeypatch,
        _PasswordClient(
            _Password(
                has_password=1,
                has_recovery="yes",
                hint=object(),
                pending_reset_date="2026-03-01",
            ),
        ),
    )

    result = await execute_read("acc-2fa", GetTwoFactorStatus())

    assert isinstance(result, TwoFactorStatusResult)
    assert (result.has_password, result.has_recovery) == (False, False)
    assert result.hint is None
    assert result.pending_reset_date is None


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        pytest.param(
            SetTwoFactorPassword(new_password=_PASSWORD, hint="hint"),
            {"current_password": None, "new_password": _PASSWORD, "hint": "hint"},
            id="set",
        ),
        pytest.param(
            SetTwoFactorPassword(current_password="old", new_password=_PASSWORD),
            {"current_password": "old", "new_password": _PASSWORD, "hint": ""},
            id="change",
        ),
        pytest.param(
            SetTwoFactorPassword(current_password=_PASSWORD),
            {"current_password": _PASSWORD, "new_password": None, "hint": ""},
            id="remove",
        ),
    ],
)
@pytest.mark.asyncio
async def test_edit_2fa_argument_shapes(
    monkeypatch: pytest.MonkeyPatch,
    action: SetTwoFactorPassword,
    expected: dict[str, object],
) -> None:
    """The field pair IS the verb, and no recovery email is ever passed."""
    client = _EditClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", action)

    assert result.status == "ok"
    assert client.calls == [expected]
    assert "email" not in client.calls[0]
    assert "email_code_callback" not in client.calls[0]


@pytest.mark.asyncio
async def test_a_false_return_is_a_no_op_not_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``edit_2fa`` returns ``False`` from a call it never made — see the module docstring."""
    patch_action_client(monkeypatch, _EditClient(result=False))

    result = await execute("acc-2fa", SetTwoFactorPassword(current_password=_PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_not_changed"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.rpcerrorlist.PasswordHashInvalidError(None), "twofa_current_password_invalid"),
        (errors.rpcerrorlist.SrpIdInvalidError(None), "twofa_current_password_invalid"),
        (errors.rpcerrorlist.SrpPasswordChangedError(None), "twofa_current_password_invalid"),
        (errors.rpcerrorlist.NewSettingsInvalidError(None), "twofa_settings_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_each_mapped_refusal_becomes_its_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: str,
) -> None:
    patch_action_client(monkeypatch, _EditClient(error=error))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == code


@pytest.mark.asyncio
async def test_an_unmapped_rpc_error_is_re_raised_for_the_generic_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the SRP/settings family is translated; everything else keeps its class."""
    patch_action_client(monkeypatch, _EditClient(error=errors.rpcerrorlist.AboutTooLongError(None)))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "AboutTooLongError"


@pytest.mark.asyncio
async def test_a_flood_wait_still_reaches_the_dedicated_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error map must not swallow the flood family (see ``_TWOFA_ERROR_CODES``)."""
    flood = errors.FloodWaitError(None)
    flood.seconds = 42
    patch_action_client(monkeypatch, _EditClient(error=flood))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 42


@pytest.mark.asyncio
async def test_no_dispatched_value_or_error_message_carries_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The credential rule, asserted rather than trusted.

    Both halves are covered: the ``ActionResult`` of a refused write (whose
    ``error_message`` the API returns verbatim) and the ``TwoFactorGatewayError``
    the dispatcher raises. Only the ``edit_2fa`` kwargs are exempt — that is the
    wire, and the password is the point of the call.
    """
    client = _EditClient(error=errors.rpcerrorlist.PasswordHashInvalidError(None))
    patch_action_client(monkeypatch, client)
    action = SetTwoFactorPassword(current_password=_PASSWORD, new_password=_PASSWORD, hint="hint")

    result = await execute("acc-2fa", action)

    assert _PASSWORD not in result.model_dump_json()
    assert client.requests == []
    with pytest.raises(TwoFactorGatewayError) as excinfo:
        await dispatch_set_twofa_password(client, action)  # ty: ignore[invalid-argument-type]
    assert _PASSWORD not in str(excinfo.value)
    assert _PASSWORD not in repr(excinfo.value.__cause__)


def test_neither_password_appears_in_the_actions_repr() -> None:
    """Both secrets carry ``repr=False``, so neither rides a rendered frame local.

    Pydantic renders every field into ``__repr__``, and ``repr()`` is exactly what an
    error tracker ships out of a stack frame's locals.
    """
    rendered = repr(
        SetTwoFactorPassword(current_password="OLD-SECRET", new_password=_PASSWORD, hint="h"),
    )

    assert "OLD-SECRET" not in rendered
    assert _PASSWORD not in rendered
    # The public half is still legible, so the repr stays useful for debugging.
    assert "hint='h'" in rendered


def test_both_passwords_none_is_refused_by_the_action() -> None:
    """That combination makes ``edit_2fa`` return ``False`` without any RPC."""
    with pytest.raises(ValueError, match="current_password/new_password"):
        SetTwoFactorPassword()
