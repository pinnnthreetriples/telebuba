"""Recovery-email dispatch tests — ``account.updatePasswordSettings`` and friends.

The load-bearing test in here is
:func:`test_the_email_write_omits_both_password_fields_so_the_password_survives`,
parametrized over BOTH modes that reach ``updatePasswordSettings``. Telethon's
``edit_2fa`` writes ``new_password_hash = b''`` whenever ``new_password`` is
falsy, and an empty hash DELETES the cloud password — so an "obvious"
simplification of this dispatcher back onto ``edit_2fa``, or a
``PasswordInputSettings`` built with ``new_password_hash=b""``, would silently
wipe the password while reporting that an email was attached. That assertion is
what stands between this feature and that bug.

``compute_check`` is stubbed. It is Telethon's own SRP implementation over
2048-bit modular arithmetic and needs a live server challenge; what these tests
own is the REQUEST SHAPE around it, not the cryptography.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest
from telethon import errors
from telethon.tl.functions.account import (
    CancelPasswordEmailRequest,
    ConfirmPasswordEmailRequest,
    GetPasswordRequest,
    ResendPasswordEmailRequest,
    UpdatePasswordSettingsRequest,
)

from core.telegram_client import execute, execute_read
from core.telegram_client._twofa import (
    _TWOFA_ERROR_CODES,
    TwoFactorGatewayError,
    dispatch_manage_twofa_email,
    twofa_log_extra,
)
from schemas.telegram_actions import GetTwoFactorStatus, ManageTwoFactorEmail
from schemas.telegram_actions_twofa import TwoFactorStatusResult
from schemas.twofa import TwoFactorRefusalCode
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PASSWORD = "stored-passphrase"
_EMAIL = "recovery@example.com"
_CODE = "424242"
_SECRETS = (_PASSWORD, _EMAIL, _CODE)


class _Algo:
    """Stand-in for ``PasswordKdfAlgo*`` — only its presence is read here."""


class _Password:
    """The ``account.Password`` attributes the email path reads."""

    def __init__(self, *, current_algo: object | None = None, **fields: object) -> None:
        self.current_algo = current_algo
        for name, value in fields.items():
            setattr(self, name, value)


class _EmailClient:
    """Records every request; optionally raises on the non-``getPassword`` one."""

    def __init__(
        self,
        *,
        password: _Password | None = None,
        error: Exception | None = None,
    ) -> None:
        self._password = password if password is not None else _Password(current_algo=_Algo())
        self._error = error
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetPasswordRequest):
            return self._password
        if self._error is not None:
            raise self._error
        return True

    def only(self, kind: type) -> Any:
        matched = [r for r in self.requests if isinstance(r, kind)]
        assert len(matched) == 1, f"expected exactly one {kind.__name__}, got {len(matched)}"
        return matched[0]


@pytest.fixture
def stub_compute_check(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, str]]:
    """Replace Telethon's SRP proof with a sentinel; record what it was asked to prove."""
    calls: list[tuple[object, str]] = []

    def _fake(pwd: object, password: str) -> str:
        calls.append((pwd, password))
        return "srp-proof"

    monkeypatch.setattr("core.telegram_client._twofa.compute_check", _fake)
    return calls


def _set_action() -> ManageTwoFactorEmail:
    return ManageTwoFactorEmail(mode="set", current_password=_PASSWORD, email=_EMAIL)


def _clear_action() -> ManageTwoFactorEmail:
    return ManageTwoFactorEmail(mode="clear", current_password=_PASSWORD)


@pytest.mark.parametrize(
    ("action", "expected_email"),
    [
        pytest.param(_set_action(), _EMAIL, id="set"),
        # ``clear`` detaches a CONFIRMED address by sending an EMPTY one. An empty
        # string is still a present TL flag (Telethon omits a field only when it is
        # ``None`` or ``False``), so this genuinely clears rather than no-ops — and it
        # must clear the email WITHOUT touching the password fields, exactly like set.
        pytest.param(_clear_action(), "", id="clear"),
    ],
)
@pytest.mark.asyncio
async def test_the_email_write_omits_both_password_fields_so_the_password_survives(
    monkeypatch: pytest.MonkeyPatch,
    stub_compute_check: list[tuple[object, str]],
    action: ManageTwoFactorEmail,
    expected_email: str,
) -> None:
    """``new_algo`` and ``new_password_hash`` must stay unset — see the module docstring."""
    client = _EmailClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-mail", action)

    assert result.status == "ok"
    request = client.only(UpdatePasswordSettingsRequest)
    assert request.new_settings.new_algo is None
    assert request.new_settings.new_password_hash is None
    assert request.new_settings.email == expected_email
    # The hint is not touched either: this call is about the email and nothing else.
    assert request.new_settings.hint is None
    assert request.password == "srp-proof"
    # The proof is computed against a FRESH challenge, never a cached one.
    assert [password for _pwd, password in stub_compute_check] == [_PASSWORD]
    assert isinstance(client.requests[0], GetPasswordRequest)


@pytest.mark.asyncio
async def test_email_unconfirmed_is_the_success_signal_carrying_the_code_length(
    stub_compute_check: list[tuple[object, str]],
) -> None:
    """``EMAIL_UNCONFIRMED_<N>`` means "attached, code of length N mailed"."""
    client = _EmailClient(error=errors.EmailUnconfirmedError(None, capture=6))

    code_length = await dispatch_manage_twofa_email(client, _set_action())  # ty: ignore[invalid-argument-type]

    assert code_length == 6
    # The settings call WAS made and authorised; only its reply was an "error".
    assert len(stub_compute_check) == 1
    assert isinstance(client.only(UpdatePasswordSettingsRequest), UpdatePasswordSettingsRequest)


@pytest.mark.asyncio
async def test_a_clean_return_means_the_address_needed_no_confirmation(
    stub_compute_check: list[tuple[object, str]],
) -> None:
    client = _EmailClient()

    code_length = await dispatch_manage_twofa_email(client, _set_action())  # ty: ignore[invalid-argument-type]

    assert code_length is None
    assert len(stub_compute_check) == 1


@pytest.mark.asyncio
async def test_the_code_length_reaches_the_action_result(
    monkeypatch: pytest.MonkeyPatch,
    stub_compute_check: list[tuple[object, str]],
) -> None:
    """The service has no other way to learn it (see ``_DispatchResult``)."""
    patch_action_client(monkeypatch, _EmailClient(error=errors.EmailUnconfirmedError(None, 8)))

    result = await execute("acc-mail", _set_action())

    assert result.status == "ok"
    assert result.twofa_email_code_length == 8
    assert len(stub_compute_check) == 1


@pytest.mark.asyncio
async def test_set_refuses_when_the_account_has_no_cloud_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``current_algo`` means 2FA is off, so a recovery email guards nothing.

    Without this guard Telethon's ``compute_check`` raises a bare ``ValueError``
    about an "unsupported password algorithm", which the executor can only report
    as the opaque ``failed``.
    """
    client = _EmailClient(password=_Password(current_algo=None))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-mail", _set_action())

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_password_not_set"
    assert [r for r in client.requests if isinstance(r, UpdatePasswordSettingsRequest)] == []


@pytest.mark.parametrize(
    ("mode", "kwargs", "request_cls"),
    [
        pytest.param("confirm", {"code": _CODE}, ConfirmPasswordEmailRequest, id="confirm"),
        pytest.param("resend", {}, ResendPasswordEmailRequest, id="resend"),
        pytest.param("cancel", {}, CancelPasswordEmailRequest, id="cancel"),
    ],
)
@pytest.mark.asyncio
async def test_each_other_mode_sends_exactly_its_own_request(
    mode: str,
    kwargs: dict[str, str],
    request_cls: type,
) -> None:
    client = _EmailClient()
    action = ManageTwoFactorEmail(mode=mode, **kwargs)  # ty: ignore[invalid-argument-type]

    code_length = await dispatch_manage_twofa_email(client, action)  # ty: ignore[invalid-argument-type]

    # Only ``set`` can learn a length; none of these three answers one.
    assert code_length is None
    # No ``getPassword`` either: only the set/clear modes need an SRP proof.
    assert [type(r) for r in client.requests] == [request_cls]
    if mode == "confirm":
        assert client.only(ConfirmPasswordEmailRequest).code == _CODE


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.rpcerrorlist.CodeInvalidError(None), "twofa_email_code_invalid"),
        (errors.rpcerrorlist.EmailHashExpiredError(None), "twofa_email_hash_expired"),
        (errors.rpcerrorlist.EmailInvalidError(None), "twofa_email_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_each_mapped_email_refusal_becomes_its_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    code: str,
) -> None:
    patch_action_client(monkeypatch, _EmailClient(error=error))

    result = await execute("acc-mail", ManageTwoFactorEmail(mode="confirm", code=_CODE))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == code


@pytest.mark.asyncio
async def test_a_flood_wait_on_an_email_call_still_reaches_the_flood_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flood = errors.FloodWaitError(None)
    flood.seconds = 17
    patch_action_client(monkeypatch, _EmailClient(error=flood))

    result = await execute("acc-mail", ManageTwoFactorEmail(mode="resend"))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 17


@pytest.mark.asyncio
async def test_get_twofa_status_reports_a_pending_email_pattern(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending address is a different fact from ``has_recovery`` and from login email."""
    patch_read_client(
        monkeypatch,
        _EmailClient(
            password=_Password(
                current_algo=_Algo(),
                has_password=True,
                has_recovery=False,
                email_unconfirmed_pattern="r**@example.com",
            ),
        ),
    )

    result = await execute_read("acc-mail", GetTwoFactorStatus())

    assert isinstance(result, TwoFactorStatusResult)
    assert result.email_unconfirmed_pattern == "r**@example.com"
    assert result.has_recovery is False


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        pytest.param(_set_action(), {"mode": "set"}, id="set"),
        pytest.param(
            ManageTwoFactorEmail(mode="confirm", code=_CODE),
            {"mode": "confirm"},
            id="confirm",
        ),
        pytest.param(ManageTwoFactorEmail(mode="cancel"), {"mode": "cancel"}, id="cancel"),
        pytest.param(_clear_action(), {"mode": "clear"}, id="clear"),
    ],
)
def test_the_log_extra_carries_the_mode_and_nothing_else(
    action: ManageTwoFactorEmail,
    expected: dict[str, str],
) -> None:
    extra = twofa_log_extra(action)

    assert extra == expected
    assert all(secret not in str(extra) for secret in _SECRETS)


@pytest.mark.asyncio
async def test_no_result_or_error_message_carries_the_password_email_or_code(
    monkeypatch: pytest.MonkeyPatch,
    stub_compute_check: list[tuple[object, str]],
) -> None:
    """All three are sensitive: the credential, the personal data and the one-time code."""
    client = _EmailClient(error=errors.rpcerrorlist.EmailInvalidError(None))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-mail", _set_action())

    assert result.status == "failed"
    for secret in _SECRETS:
        assert secret not in result.model_dump_json()
    with pytest.raises(TwoFactorGatewayError) as excinfo:
        await dispatch_manage_twofa_email(client, _set_action())  # ty: ignore[invalid-argument-type]
    for secret in _SECRETS:
        assert secret not in str(excinfo.value)
    assert len(stub_compute_check) == 2


def test_none_of_the_three_secrets_appears_in_the_actions_repr() -> None:
    """All three carry ``repr=False``, so none of them rides a rendered frame local.

    Pydantic renders every field into ``__repr__``, and ``repr()`` is exactly what an
    error tracker ships out of a stack frame's locals.
    """
    rendered = repr(
        ManageTwoFactorEmail(mode="confirm", current_password=_PASSWORD, email=_EMAIL, code=_CODE),
    )

    for secret in _SECRETS:
        assert secret not in rendered
    # The bounded half is still legible, so the repr stays useful for debugging.
    assert "mode='confirm'" in rendered


def test_every_refusal_code_is_a_member_of_the_enumerated_vocabulary() -> None:
    """Two-way tripwire so a rename cannot slip past the i18n parity guard.

    ``tests/test_error_code_i18n_parity`` enumerates ``TwoFactorRefusalCode``, which
    only helps while the ``Literal`` and the code actually agree. Three of these
    codes are raised by hand rather than through the ladder — one of them from
    ``services/`` — so nothing else connects them to that guard.
    """
    declared = set(get_args(TwoFactorRefusalCode))
    mapped = {code for _cls, code in _TWOFA_ERROR_CODES}
    assert mapped <= declared, f"ladder codes missing from the Literal: {sorted(mapped - declared)}"

    sources = "".join(
        (_REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in ("core/telegram_client/_twofa.py", "services/accounts/twofa.py")
    )
    unused = sorted(code for code in declared if f'"{code}"' not in sources)
    assert unused == [], f"declared but raised nowhere: {unused}"


def test_the_action_refuses_a_mode_whose_required_fields_are_missing() -> None:
    with pytest.raises(ValueError, match="requires current_password and email"):
        ManageTwoFactorEmail(mode="set")
    with pytest.raises(ValueError, match="mode='clear' requires current_password"):
        ManageTwoFactorEmail(mode="clear")
    with pytest.raises(ValueError, match="requires code"):
        ManageTwoFactorEmail(mode="confirm")
