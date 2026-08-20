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

from core.telegram_client import UNCONFIRMED_ERROR_TYPE, execute, execute_read
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
    """``edit_2fa`` EMULATED, not merely recorded, plus the pre-flight ``getPassword``.

    A ``**kwargs`` double asserts only what the caller passed in, which is no
    assertion at all. This one takes exactly the arguments the dispatcher is allowed
    to use — a stray one is a ``TypeError`` here, as it would be against the real
    client — and it refuses ``email`` / ``email_code_callback`` outright: an ``email``
    through ``edit_2fa`` would delete the cloud password (empty ``new_password_hash``),
    and a callback cannot read a mailbox from an unattended backend.

    ``email_unconfirmed`` reproduces Telethon 1.44's own clause for that answer, which
    handles it by CALLING ``email_code_callback`` — so with no callback passed, the
    exception that escapes is the ``'NoneType' object is not callable`` raised while
    handling the Telegram error, not the Telegram error. Nothing but reproducing the
    clause can show that.
    """

    def __init__(
        self,
        *,
        result: bool = True,
        error: Exception | None = None,
        password: object = None,
        preflight_error: Exception | None = None,
        email_unconfirmed: int | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self._password = password
        self._preflight_error = preflight_error
        self._email_unconfirmed = email_unconfirmed
        self.calls: list[Mapping[str, Any]] = []
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        assert isinstance(request, GetPasswordRequest), "only the pre-flight read is raw"
        if self._preflight_error is not None:
            raise self._preflight_error
        return self._password

    async def edit_2fa(
        self,
        *,
        current_password: str | None = None,
        new_password: str | None = None,
        hint: str = "",
        email: str | None = None,
        email_code_callback: Any = None,
    ) -> bool:
        self.calls.append(
            {"current_password": current_password, "new_password": new_password, "hint": hint},
        )
        assert email is None, "an email through edit_2fa would wipe the cloud password"
        assert email_code_callback is None, "this backend cannot read a mailbox"
        if self._email_unconfirmed is not None:
            unconfirmed = errors.EmailUnconfirmedError(None, capture=self._email_unconfirmed)
            try:
                raise unconfirmed
            except errors.EmailUnconfirmedError as exc:
                # Telethon's line, verbatim in effect: it calls the callback it was
                # handed, and this dispatcher hands it none.
                email_code_callback(exc.code_length)
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
    """The field pair IS the verb, and no recovery email is ever passed.

    The double refuses ``email`` / ``email_code_callback`` itself, so those two are
    asserted by construction rather than by looking for absent dict keys.
    """
    client = _EditClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", action)

    assert result.status == "ok"
    assert client.calls == [expected]
    # The pre-flight read runs FIRST and is the only raw request this path makes:
    # everything after it can have left the process, everything before it cannot.
    assert [type(request) for request in client.requests] == [GetPasswordRequest]


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
        (errors.rpcerrorlist.NewSaltInvalidError(None), "twofa_settings_invalid"),
        (errors.rpcerrorlist.PasswordMissingError(None), "twofa_password_not_set"),
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
async def test_a_dead_pre_flight_read_is_a_plain_failure_not_a_maybe_applied_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window this pre-flight exists to close.

    ``execute`` decides "was it dispatched?" from ``client is not None``, so before the
    pre-flight a socket dying on ``edit_2fa``'s OWN opening ``getPassword`` was
    reported as ``unavailable`` / ``UnconfirmedRequest`` — "Telegram may have applied
    this password" — for a call that had sent nothing. Waking a pooled client makes
    that the LIKELIEST failure, not the rarest, and the service persists an
    unconfirmed password on the strength of it.
    """
    client = _EditClient(preflight_error=ConnectionError("socket died"))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_state_unreadable"
    # Nothing was written, and the assertion that proves it: no write was attempted.
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_socket_death_after_the_pre_flight_is_still_reported_as_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the narrowing: the genuinely ambiguous window must survive.

    Once the pre-flight answered, the next failure really can have taken only the
    REPLY to a write, so it has to keep reaching ``execute``'s ``dispatched`` arm.
    """
    patch_action_client(monkeypatch, _EditClient(error=ConnectionError("socket died")))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "unavailable"
    assert result.error_type == UNCONFIRMED_ERROR_TYPE


@pytest.mark.asyncio
async def test_a_pending_recovery_email_does_not_crash_the_password_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``EMAIL_UNCONFIRMED`` is the same "applied" answer both 2FA paths must read.

    Reachable whenever a recovery-email verification is still pending while the
    password is changed — which is exactly why Telethon has the clause. Its handling
    of it calls an ``email_code_callback`` this path deliberately does not pass, so the
    answer the email path treats as SUCCESS used to surface here as
    ``'NoneType' object is not callable`` → an opaque ``failed``, with the accepted
    password never persisted.
    """
    client = _EditClient(email_unconfirmed=6)
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "ok"
    assert result.error_message is None
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_a_real_type_error_is_not_swallowed_as_a_pending_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a ``TypeError`` raised while handling that Telegram error means "applied"."""
    patch_action_client(monkeypatch, _EditClient(error=TypeError("genuinely broken")))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TypeError"


@pytest.mark.parametrize(
    ("action", "current_hint", "expected"),
    [
        pytest.param(
            SetTwoFactorPassword(new_password=_PASSWORD), "the usual", "the usual", id="kept"
        ),
        pytest.param(
            SetTwoFactorPassword(new_password=_PASSWORD, hint=""),
            "the usual",
            "",
            id="cleared",
        ),
        pytest.param(
            SetTwoFactorPassword(new_password=_PASSWORD, hint="fresh"),
            "the usual",
            "fresh",
            id="replaced",
        ),
        pytest.param(SetTwoFactorPassword(new_password=_PASSWORD), None, "", id="none-to-keep"),
    ],
)
@pytest.mark.asyncio
async def test_an_omitted_hint_keeps_the_one_telegram_shows(
    monkeypatch: pytest.MonkeyPatch,
    action: SetTwoFactorPassword,
    current_hint: str | None,
    expected: str,
) -> None:
    """``updatePasswordSettings`` always writes the field, so "omitted" cannot mean "".

    A change that mentions no hint used to erase the hint the operator set. ``None``
    now means keep — resolved against the pre-flight read, which is the only place the
    live value exists — and ``""`` is the deliberate clear.
    """
    client = _EditClient(password=_Password(hint=current_hint))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", action)

    assert result.status == "ok"
    assert [call["hint"] for call in client.calls] == [expected]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(errors.rpcerrorlist.SessionTooFreshError(None, 900), id="session"),
        pytest.param(errors.rpcerrorlist.PasswordTooFreshError(None, 900), id="password"),
    ],
)
@pytest.mark.asyncio
async def test_a_too_fresh_refusal_reaches_the_operator_with_its_wait(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    """Both are 400s that carry ``.seconds``, so they bypass the flood clauses.

    ``SESSION_TOO_FRESH`` is *the* refusal for "this session just signed in and is now
    setting a cloud password" — this dashboard's normal workflow — and it used to
    reach the operator as a blank ``failed`` with the duration dropped. Mapping it into
    ``_TWOFA_ERROR_CODES`` would drop it too, hence the ladder.
    """
    patch_action_client(monkeypatch, _EditClient(error=error))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=_PASSWORD))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 900


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
    # The one raw request this path sends is the pre-flight read, which carries no
    # fields at all — the password exists only in the exempt ``edit_2fa`` kwargs.
    assert [type(request) for request in client.requests] == [GetPasswordRequest]
    assert _PASSWORD not in str(client.requests)
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
