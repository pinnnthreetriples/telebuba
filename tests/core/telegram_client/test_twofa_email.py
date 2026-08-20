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
from telethon.extensions import BinaryReader
from telethon.tl.functions.account import (
    CancelPasswordEmailRequest,
    ConfirmPasswordEmailRequest,
    GetPasswordRequest,
    ResendPasswordEmailRequest,
    UpdatePasswordSettingsRequest,
)
from telethon.tl.types.account import PasswordInputSettings

from core.telegram_client import execute, execute_read
from core.telegram_client._twofa import (
    _TWOFA_ERROR_CODES,
    TwoFactorGatewayError,
    twofa_log_extra,
)
from core.telegram_client._twofa_email import dispatch_manage_twofa_email
from schemas.telegram_actions import (
    GetTwoFactorStatus,
    ManageTwoFactorEmail,
    SetTwoFactorPassword,
)
from schemas.telegram_actions_twofa import TwoFactorStatusResult
from schemas.twofa import TwoFactorRefusalCode
from tests.core.telegram_client._twofa_doubles import algo as _algo
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PASSWORD = "stored-passphrase"
_EMAIL = "recovery@example.com"
_CODE = "424242"
_SECRETS = (_PASSWORD, _EMAIL, _CODE)
# ``account.passwordInputSettings``: ``new_algo`` / ``new_password_hash`` / ``hint``
# all sit behind flag 0 and ``email`` behind flag 1, so an email-only write is 2.
_EMAIL_ONLY_FLAGS = 2


# ``_algo`` is the shared REAL ``PasswordKdfAlgo*`` factory, not a stand-in any more:
# ``require_fast_algo`` admits only the ``(p, g)`` Telethon short-circuits on, so a
# placeholder would be refused before the proof is computed. See ``_twofa_doubles``.


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
        read_error: Exception | None = None,
    ) -> None:
        self._password = password if password is not None else _Password(current_algo=_algo())
        self._error = error
        self._read_error = read_error
        self.requests: list[object] = []

    async def connect(self) -> None:
        return None

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, GetPasswordRequest):
            if self._read_error is not None:
                raise self._read_error
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

    monkeypatch.setattr("core.telegram_client._twofa_email.compute_check", _fake)
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
    # SERIALISED, not just read off the Python object. Everything above inspects
    # attributes, and the claim this test is named for is about the WIRE: the three
    # password fields share TL flag 0 and the email is flag 1, so "email only" is the
    # single value 2 — and Telethon's own ``_bytes`` assert (all three false-y or all
    # true-y) is what would reject a hash-only settings object. Round-tripped because
    # that is the only way to show ``clear``'s empty string is a PRESENT flag rather
    # than an omitted one: an omitted field reads back as ``None``, not as ``""``.
    wire = bytes(request.new_settings)
    assert int.from_bytes(wire[4:8], "little") == _EMAIL_ONLY_FLAGS
    decoded = BinaryReader(wire).tgread_object()
    assert isinstance(decoded, PasswordInputSettings)
    assert decoded.email == expected_email
    assert (decoded.new_algo, decoded.new_password_hash, decoded.hint) == (None, None, None)


@pytest.mark.asyncio
async def test_email_unconfirmed_is_the_success_signal_carrying_the_code_length(
    stub_compute_check: list[tuple[object, str]],
) -> None:
    """``EMAIL_UNCONFIRMED_<N>`` means "attached, code of length N mailed"."""
    client = _EmailClient(error=errors.EmailUnconfirmedError(None, capture=6))

    outcome = await dispatch_manage_twofa_email(client, _set_action())  # ty: ignore[invalid-argument-type]

    assert outcome.twofa_email_code_length == 6
    assert outcome.twofa_email_unconfirmed is True
    # The settings call WAS made and authorised; only its reply was an "error".
    assert len(stub_compute_check) == 1
    assert isinstance(client.only(UpdatePasswordSettingsRequest), UpdatePasswordSettingsRequest)


@pytest.mark.asyncio
async def test_the_bare_email_unconfirmed_reports_no_length_rather_than_zero(
    stub_compute_check: list[tuple[object, str]],
) -> None:
    """Telethon maps a suffix-less ``EMAIL_UNCONFIRMED`` to ``code_length = 0``.

    Zero is not a length. Passed through, it reaches the card as ``maxLength={0}`` —
    an input nobody can type into, next to a Confirm button that can never enable,
    because ``?? null`` does not catch ``0``. ``None`` means "Telegram did not say",
    which is the truth, and the pending address still shows up on the next status
    read as ``email_unconfirmed_pattern``.
    """
    client = _EmailClient(error=errors.EmailUnconfirmedError(None))

    outcome = await dispatch_manage_twofa_email(client, _set_action())  # ty: ignore[invalid-argument-type]

    assert outcome.twofa_email_code_length is None
    # ``None`` is the LENGTH being unknown, never the address not being pending: the
    # flag is threaded separately for exactly that reason, and
    # ``tests/services/accounts/test_twofa_email.py`` asserts what the service then
    # reports end to end.
    assert outcome.twofa_email_unconfirmed is True
    assert len(stub_compute_check) == 1


@pytest.mark.asyncio
async def test_an_unusable_srp_challenge_becomes_one_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``compute_check`` says everything with a bare ``ValueError``.

    Unimplemented algorithm class, bad p/g, bad B, bad g_b — four different messages,
    all of them Telethon internals, none of them an ``RPCError``, so all four used to
    reach the operator as the opaque ``failed`` while the log carried the prose.
    """

    def _unusable(pwd: object, password: str) -> object:  # noqa: ARG001
        msg = "bad p/g in password"
        raise ValueError(msg)

    monkeypatch.setattr("core.telegram_client._twofa_email.compute_check", _unusable)
    client = _EmailClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-mail", _set_action())

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_password_algo_unsupported"
    # Refused before the write, so nothing about the account changed.
    assert [r for r in client.requests if isinstance(r, UpdatePasswordSettingsRequest)] == []


@pytest.mark.asyncio
async def test_a_clean_return_means_the_address_needed_no_confirmation(
    stub_compute_check: list[tuple[object, str]],
) -> None:
    client = _EmailClient()

    outcome = await dispatch_manage_twofa_email(client, _set_action())  # ty: ignore[invalid-argument-type]

    assert outcome.twofa_email_code_length is None
    assert outcome.twofa_email_unconfirmed is False
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
    assert result.twofa_email_unconfirmed is True
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

    outcome = await dispatch_manage_twofa_email(client, action)  # ty: ignore[invalid-argument-type]

    # Only ``set`` can learn a length; none of these three answers one.
    assert outcome.twofa_email_code_length is None
    assert outcome.twofa_email_unconfirmed is False
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
                current_algo=_algo(),
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
        pytest.param(
            SetTwoFactorPassword(new_password=_PASSWORD, hint="h"),
            {"has_hint": True, "removing": False},
            id="set-with-hint",
        ),
        pytest.param(
            SetTwoFactorPassword(new_password=_PASSWORD),
            {"has_hint": False, "removing": False},
            id="set",
        ),
        pytest.param(
            SetTwoFactorPassword(current_password=_PASSWORD),
            {"has_hint": False, "removing": True},
            id="remove",
        ),
    ],
)
def test_the_password_log_extra_says_what_kind_of_write_it_was_and_nothing_more(
    action: SetTwoFactorPassword,
    expected: dict[str, object],
) -> None:
    """The PASSWORD branch of the same function: every field it carries is a secret.

    Lives beside the email branch because they are two arms of one ``twofa_log_extra``
    — and because nothing pinned this arm at all, so a mutant returning the email
    shape for a password write killed no test.
    """
    extra = twofa_log_extra(action)

    assert extra == expected
    assert _PASSWORD not in str(extra)


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


@pytest.mark.parametrize(
    "dead",
    [
        pytest.param(ConnectionError("socket died"), id="connection"),
        pytest.param(TimeoutError("read timed out"), id="timeout"),
    ],
)
@pytest.mark.asyncio
async def test_a_dead_srp_read_on_the_email_path_is_a_plain_failure(
    monkeypatch: pytest.MonkeyPatch,
    stub_compute_check: list[tuple[object, str]],
    dead: Exception,
) -> None:
    """The bug ``_password_state`` exists to fix, which this path had left unfixed.

    This read is the FIRST thing the write does, so a socket dying on it proves
    nothing was sent. Issued bare, it escaped to ``execute``'s ``dispatched = client
    is not None`` arm and was stamped ``UNCONFIRMED_ERROR_TYPE`` — "Telegram may have
    applied this request" — for a call that never left the process. The password half
    routed through ``_password_state`` for exactly this; the email half did not.
    """
    client = _EmailClient(read_error=dead)
    patch_action_client(monkeypatch, client)

    result = await execute("acc-mail", _set_action())

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_state_unreadable"
    # Nothing was computed and nothing was sent: the read is the only request.
    assert [type(request) for request in client.requests] == [GetPasswordRequest]
    assert stub_compute_check == []


def test_every_refusal_code_is_a_member_of_the_enumerated_vocabulary() -> None:
    """Two-way tripwire so a rename cannot slip past the i18n parity guard.

    ``tests/test_error_code_i18n_parity`` enumerates ``TwoFactorRefusalCode``, which
    only helps while the ``Literal`` and the code actually agree. Three of these
    codes are raised by hand rather than through the ladder — two of them from
    ``services/`` and two from the extracted SRP sibling — so nothing else connects
    them to that guard.
    """
    declared = set(get_args(TwoFactorRefusalCode))
    mapped = {code for _cls, code in _TWOFA_ERROR_CODES}
    assert mapped <= declared, f"ladder codes missing from the Literal: {sorted(mapped - declared)}"

    sources = "".join(
        (_REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "core/telegram_client/_twofa.py",
            "core/telegram_client/_twofa_email.py",
            "core/telegram_client/_twofa_srp.py",
            "services/accounts/twofa.py",
            "services/accounts/_twofa_email.py",
        )
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
