"""Cloud-password dispatch tests — ``account.getPassword`` / ``updatePasswordSettings``.

The request SHAPE of the three password verbs plus the live status read. HOW the
SRP work is called — off the loop thread, bounded, salted — is the sibling
``test_twofa_srp.py``; the doubles both use live in ``_twofa_doubles.py``, whose
docstring explains why the client is a raw one with no ``edit_2fa`` to fall back
on.

The password is a credential, so one test here is not about behaviour at all:
:func:`test_no_dispatched_value_or_error_message_carries_the_password` walks
everything the module produced and asserts the secret is in none of it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from telethon import errors
from telethon.extensions import BinaryReader
from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
from telethon.tl.types import InputCheckPasswordEmpty
from telethon.tl.types.account import PasswordInputSettings

from core.telegram_client import UNCONFIRMED_ERROR_TYPE, execute, execute_read
from core.telegram_client._twofa import TwoFactorGatewayError, dispatch_set_twofa_password
from schemas.telegram_actions import GetTwoFactorStatus, SetTwoFactorPassword
from schemas.telegram_actions_twofa import TwoFactorStatusResult
from tests.core.telegram_client._twofa_doubles import (
    DIGEST,
    PASSWORD,
    PROOF,
    Password,
    PasswordClient,
    RawClient,
    patch_srp,
)
from tests.core.telegram_client.helpers import patch_action_client, patch_read_client

# ``account.passwordInputSettings``: ``new_algo`` / ``new_password_hash`` / ``hint``
# share flag 0 and ``email`` is flag 1, so a password write with no email is 1.
_PASSWORD_ONLY_FLAGS = 1


@pytest.fixture
def stub_srp(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    return patch_srp(monkeypatch)


@pytest.mark.asyncio
async def test_get_twofa_status_maps_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_at = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    client = PasswordClient(
        Password(
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
    patch_read_client(monkeypatch, PasswordClient(Password()))

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
        PasswordClient(
            Password(
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
            SetTwoFactorPassword(new_password=PASSWORD, hint="hint"),
            (None, PASSWORD, "hint"),
            id="set",
        ),
        pytest.param(
            SetTwoFactorPassword(current_password="old", new_password=PASSWORD),
            ("old", PASSWORD, "the usual"),
            id="change",
        ),
        pytest.param(
            SetTwoFactorPassword(current_password=PASSWORD),
            (PASSWORD, None, ""),
            id="remove",
        ),
    ],
)
@pytest.mark.asyncio
async def test_each_verb_sends_exactly_one_read_and_one_write(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
    action: SetTwoFactorPassword,
    expected: tuple[str | None, str | None, str],
) -> None:
    """The field pair IS the verb, and the whole operation costs two RPCs.

    Two, not three: ``edit_2fa`` opened with a ``getPassword`` of its own on top of
    the pre-flight one this module used to do, so every password operation read the
    state twice — and the pre-flight was the leg that ate a ``FLOOD_WAIT`` before
    anything had been attempted. It is also what made a dead read indistinguishable
    from a dead write, since the socket simply died one leg later.
    """
    checked, hashed, hint = expected
    client = RawClient(password=Password(has_password=True, hint="the usual"))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", action)

    assert result.status == "ok"
    assert [type(request) for request in client.requests] == [
        GetPasswordRequest,
        UpdatePasswordSettingsRequest,
    ]
    written = client.written()
    # ``InputCheckPasswordEmpty`` is what "no current password" looks like on the
    # wire; anything else means ``compute_check`` authorised the write.
    if checked is None:
        assert isinstance(written.password, InputCheckPasswordEmpty)
        assert stub_srp["check"] == []
    else:
        assert written.password == PROOF
        assert [password for _pwd, password in stub_srp["check"]] == [checked]
    assert written.new_settings.new_password_hash == (b"" if hashed is None else DIGEST)
    expected_digests = [] if hashed is None else [hashed]
    assert [password for _algo, password in stub_srp["digest"]] == expected_digests
    assert written.new_settings.hint == hint


@pytest.mark.parametrize(
    ("action", "expected_hash"),
    [
        pytest.param(SetTwoFactorPassword(new_password=PASSWORD, hint="h"), DIGEST, id="set"),
        pytest.param(
            SetTwoFactorPassword(current_password="old", new_password=PASSWORD),
            DIGEST,
            id="change",
        ),
        pytest.param(SetTwoFactorPassword(current_password=PASSWORD), b"", id="remove"),
    ],
)
@pytest.mark.asyncio
async def test_only_a_removal_puts_an_empty_password_hash_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the stubs are the point, not the record
    action: SetTwoFactorPassword,
    expected_hash: bytes,
) -> None:
    """The sibling of the email path's load-bearing test, from the other side.

    There, ``new_algo`` / ``new_password_hash`` must be ABSENT, because a
    present-but-empty hash deletes the cloud password. Here exactly one verb — the
    removal — may send that empty hash, and it may because deleting the password is
    what it is FOR. ``edit_2fa`` wrote ``b''`` for every falsy ``new_password``, which
    made the deletion a default; this asserts it is now the verb.

    SERIALISED rather than read off the Python object: the claim is about the WIRE.
    All three password fields share TL flag 0 and the email is flag 1, so a password
    write with no email is the single value 1, and the empty hash has to survive the
    round trip as ``b""`` rather than coming back as an omitted ``None``.
    """
    client = RawClient()
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", action)

    assert result.status == "ok"
    wire = bytes(client.written().new_settings)
    assert int.from_bytes(wire[4:8], "little") == _PASSWORD_ONLY_FLAGS
    decoded = BinaryReader(wire).tgread_object()
    assert isinstance(decoded, PasswordInputSettings)
    assert decoded.new_password_hash == expected_hash
    assert decoded.email is None
    assert decoded.new_algo is not None


@pytest.mark.asyncio
async def test_a_removal_against_an_account_with_no_password_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
) -> None:
    """Telethon's degrade, and why its ``False`` return was never a success.

    With ``has_password`` false there is nothing for a current password to authorise,
    so ``edit_2fa`` dropped it and then removed nothing at all. Reporting that as
    done would tell the operator 2FA is off while the account is untouched; the
    service's stale branch is what actually resolves this state, without an RPC.
    """
    client = RawClient(password=Password(has_password=False))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(current_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_not_changed"
    assert [type(request) for request in client.requests] == [GetPasswordRequest]
    assert stub_srp["check"] == []


@pytest.mark.asyncio
async def test_a_change_against_an_account_with_no_password_degrades_to_a_set(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
) -> None:
    """The other half of the same degrade: a stale stored password is not sent.

    Reachable whenever the dashboard holds a password Telegram no longer has. There
    is no challenge to compute a proof against, so the current password is dropped
    and the write becomes a plain set — which is what the operator asked for.
    """
    client = RawClient(password=Password(has_password=False))
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc-2fa",
        SetTwoFactorPassword(current_password="stale", new_password=PASSWORD),
    )

    assert result.status == "ok"
    assert isinstance(client.written().password, InputCheckPasswordEmpty)
    assert stub_srp["check"] == []
    assert [password for _algo, password in stub_srp["digest"]] == [PASSWORD]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (errors.rpcerrorlist.PasswordHashInvalidError(None), "twofa_current_password_invalid"),
        (errors.rpcerrorlist.SrpIdInvalidError(None), "twofa_current_password_invalid"),
        (errors.rpcerrorlist.SrpPasswordChangedError(None), "twofa_current_password_invalid"),
        (errors.rpcerrorlist.NewSettingsInvalidError(None), "twofa_settings_invalid"),
        (errors.rpcerrorlist.NewSaltInvalidError(None), "twofa_settings_invalid"),
        (errors.rpcerrorlist.PasswordMissingError(None), "twofa_password_not_set"),
        # ``NEW_SETTINGS_EMPTY`` is the same fact one call later: reachable when the
        # password disappears between the ONE read and the write.
        (errors.rpcerrorlist.NewSettingsEmptyError(None), "twofa_password_not_set"),
    ],
)
@pytest.mark.asyncio
async def test_each_mapped_refusal_becomes_its_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
    error: Exception,
    code: str,
) -> None:
    patch_action_client(monkeypatch, RawClient(error=error))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == code


@pytest.mark.parametrize(
    "dead",
    [
        pytest.param(ConnectionError("socket died"), id="connection"),
        # The other half of ``_password_state``'s except tuple, and not decoration: a
        # pooled client waking up answers a read with a TIMEOUT at least as often as
        # with a reset, and ``execute``'s ``dispatched`` arm keys off both alike.
        pytest.param(TimeoutError("read timed out"), id="timeout"),
    ],
)
@pytest.mark.asyncio
async def test_a_dead_read_is_a_plain_failure_not_a_maybe_applied_write(
    monkeypatch: pytest.MonkeyPatch,
    dead: Exception,
) -> None:
    """The window the ONE read closes, and the previous double could not show.

    ``execute`` decides "was it dispatched?" from ``client is not None``, so a socket
    dying anywhere inside the dispatcher is reported as ``unavailable`` /
    ``UnconfirmedRequest`` — "Telegram may have applied this password" — and the
    service persists an unconfirmed password on the strength of that. A pre-flight
    read only MOVED that window while ``edit_2fa`` still issued its own
    ``getPassword``, and a double without that internal read made the half-fix look
    complete. Waking a pooled client makes this the LIKELIEST failure, not the
    rarest.
    """
    client = RawClient(read_error=dead)
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_state_unreadable"
    # The assertion that proves nothing was written: the read is the only request.
    assert [type(request) for request in client.requests] == [GetPasswordRequest]


@pytest.mark.asyncio
async def test_a_socket_death_on_the_write_is_still_reported_as_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """The other side of the narrowing: the genuinely ambiguous window must survive.

    Once the read answered, the next failure really can have taken only the REPLY to
    a write, so it has to keep reaching ``execute``'s ``dispatched`` arm.
    """
    patch_action_client(monkeypatch, RawClient(error=ConnectionError("socket died")))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "unavailable"
    assert result.error_type == UNCONFIRMED_ERROR_TYPE


@pytest.mark.asyncio
async def test_a_pending_recovery_email_is_settled_by_one_confirming_read(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """``EMAIL_UNCONFIRMED`` on a password write is neither a crash nor an assumption.

    Reachable whenever a recovery-email verification is still pending while the
    password is set. Through ``edit_2fa`` it arrived as the ``TypeError`` Telethon
    raised while calling the code callback this backend cannot provide — an opaque
    ``failed`` with the accepted password never persisted.

    The previous round then justified reporting it as unconfirmed with "TDLib holds
    its ``last_set_password_`` until the mailed code is typed back". That member does
    not exist anywhere in TDLib; the sentence was fabricated. TDLib's own contract
    holds a change pending only when a NEW recovery email rides the same call, and
    this write carries none — so the ambiguity is settled the way TDLib settles it,
    by treating the answer as success and RE-READING the live state.
    """
    client = RawClient(
        error=errors.EmailUnconfirmedError(None, 6),
        rereads=(Password(has_password=True),),
    )
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "ok"
    assert result.error_message is None
    # Confirmed BY THE READ; without the read this is an assumption again.
    assert client.reads() == 2
    assert result.twofa_email_unconfirmed is False
    # Advisory, and threaded rather than dropped: the number exists nowhere but
    # inside this error, and the recovery-email sibling has always carried it.
    assert result.twofa_email_code_length == 6


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(ConnectionError("socket died"), id="read-failed"),
        pytest.param(None, id="no-password"),
    ],
)
@pytest.mark.asyncio
async def test_a_confirming_read_that_proves_nothing_stays_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
    answer: Exception | None,
) -> None:
    """The conservative leg: an unknown must never be upgraded into a confirmation.

    Either the confirming read never came back, or it says Telegram holds no password
    at all. Both leave "is this password in force" open, so the write is reported
    applied-but-unconfirmed and the service keeps the value while flagging it.
    """
    reread = answer if answer is not None else Password(has_password=False)
    client = RawClient(error=errors.EmailUnconfirmedError(None, 6), rereads=(reread,))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "ok"
    assert client.reads() == 2
    assert result.twofa_email_unconfirmed is True


@pytest.mark.asyncio
async def test_a_removal_answered_email_unconfirmed_is_a_refusal_never_an_ok(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """The CRITICAL verb gate: this handler must never report a REMOVAL as applied.

    Reported as ``ok`` — which is what a verb-blind handler does — the removal path
    is unrecoverable: ``remove_account_twofa`` hands the result straight to
    ``raise_for_result``, never reads ``twofa_email_unconfirmed`` (only
    ``set_account_twofa`` does), goes on to clear the column and tells the operator
    2FA is off. If the write was not in force the dashboard has just destroyed the
    only copy of a live cloud password, and nothing can recover it.

    So a removal gets its own stable code, and no confirming read is spent: nothing a
    re-read could prove would make clearing the column safe here.
    """
    client = RawClient(error=errors.EmailUnconfirmedError(None, 6))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(current_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_removal_unconfirmed"
    assert client.reads() == 1


@pytest.mark.asyncio
async def test_a_write_needing_a_proof_is_refused_when_no_current_algo_exists(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
) -> None:
    """The mirror of the recovery-email sibling's guard, which this path had missed.

    ``compute_check`` opens with ``request.current_algo``
    (``telethon/password.py:137``) and every field on ``account.Password`` is an
    optional TL flag, so an absent one raises ``AttributeError`` — a class outside
    this module's ladder, which reaches the operator as Telethon prose about a call
    that carried a plaintext password. It is refused before the proof instead.
    """
    client = RawClient(password=Password(has_password=True, current_algo=None))
    patch_action_client(monkeypatch, client)

    result = await execute(
        "acc-2fa",
        SetTwoFactorPassword(current_password="old", new_password=PASSWORD),
    )

    assert result.status == "failed"
    assert result.error_type == "TwoFactorGatewayError"
    assert result.error_message == "twofa_password_not_set"
    assert [type(request) for request in client.requests] == [GetPasswordRequest]
    assert stub_srp["check"] == []


@pytest.mark.parametrize(
    ("action", "current_hint", "expected"),
    [
        pytest.param(
            SetTwoFactorPassword(new_password=PASSWORD), "the usual", "the usual", id="kept"
        ),
        pytest.param(
            SetTwoFactorPassword(new_password=PASSWORD, hint=""),
            "the usual",
            "",
            id="cleared",
        ),
        pytest.param(
            SetTwoFactorPassword(new_password=PASSWORD, hint="fresh"),
            "the usual",
            "fresh",
            id="replaced",
        ),
        pytest.param(SetTwoFactorPassword(new_password=PASSWORD), None, "", id="none-to-keep"),
    ],
)
@pytest.mark.asyncio
async def test_an_omitted_hint_keeps_the_one_telegram_shows(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
    action: SetTwoFactorPassword,
    current_hint: str | None,
    expected: str,
) -> None:
    """``updatePasswordSettings`` always writes the field, so "omitted" cannot mean "".

    A change that mentions no hint used to erase the hint the operator set. ``None``
    now means keep — resolved against the one read, which is the only place the live
    value exists — and ``""`` is the deliberate clear.
    """
    client = RawClient(password=Password(has_password=True, hint=current_hint))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", action)

    assert result.status == "ok"
    assert client.written().new_settings.hint == expected


@pytest.mark.asyncio
async def test_a_removal_resolves_no_hint_at_all(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """``hint=None`` means KEEP only for a set/change. A removal has nothing to keep.

    The three-valued hint was introduced so a change that mentions no hint cannot
    erase the live one — but ``remove_account_twofa`` builds its action from
    ``current_password`` alone, so its hint is ``None`` too, and resolving THAT
    against the live read ships a non-empty hint alongside the empty hash: a
    combination this never sent before ``None`` came to mean "keep", and one Telegram
    can refuse as ``NEW_SETTINGS_INVALID`` — which would break removal for every
    account that has a hint.
    """
    client = RawClient(password=Password(has_password=True, hint="the usual"))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(current_password=PASSWORD))

    assert result.status == "ok"
    assert client.written().new_settings.hint == ""


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
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
    error: Exception,
) -> None:
    """Both are 400s that carry ``.seconds``, so they bypass the flood clauses.

    ``SESSION_TOO_FRESH`` is *the* refusal for "this session just signed in and is now
    setting a cloud password" — this dashboard's normal workflow — and it used to
    reach the operator as a blank ``failed`` with the duration dropped. Mapping it into
    ``_TWOFA_ERROR_CODES`` would drop it too, hence the ladder.
    """
    patch_action_client(monkeypatch, RawClient(error=error))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 900


@pytest.mark.asyncio
async def test_an_unmapped_rpc_error_is_re_raised_for_the_generic_ladder(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """Only the SRP/settings family is translated; everything else keeps its class."""
    patch_action_client(
        monkeypatch,
        RawClient(error=errors.rpcerrorlist.AboutTooLongError(None)),
    )

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "failed"
    assert result.error_type == "AboutTooLongError"


@pytest.mark.asyncio
async def test_a_flood_wait_still_reaches_the_dedicated_ladder(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """The error map must not swallow the flood family (see ``_TWOFA_ERROR_CODES``)."""
    flood = errors.FloodWaitError(None)
    flood.seconds = 42
    patch_action_client(monkeypatch, RawClient(error=flood))

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.status == "flood_wait"
    assert result.flood_wait_seconds == 42


@pytest.mark.asyncio
async def test_no_dispatched_value_or_error_message_carries_the_password(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """The credential rule, asserted rather than trusted.

    Both halves are covered: the ``ActionResult`` of a refused write (whose
    ``error_message`` the API returns verbatim) and the ``TwoFactorGatewayError``
    the dispatcher raises. Nothing is exempt any more — the previous version had to
    excuse the ``edit_2fa`` kwargs, and what goes on the wire now is an SRP proof and
    a digest, neither of which is the password.
    """
    client = RawClient(error=errors.rpcerrorlist.PasswordHashInvalidError(None))
    patch_action_client(monkeypatch, client)
    action = SetTwoFactorPassword(current_password=PASSWORD, new_password=PASSWORD, hint="hint")

    result = await execute("acc-2fa", action)

    assert PASSWORD not in result.model_dump_json()
    assert PASSWORD not in str(client.requests)
    with pytest.raises(TwoFactorGatewayError) as excinfo:
        await dispatch_set_twofa_password(client, action)  # ty: ignore[invalid-argument-type]
    assert PASSWORD not in str(excinfo.value)
    assert PASSWORD not in repr(excinfo.value.__cause__)


@pytest.mark.asyncio
async def test_the_result_reports_the_hint_the_gateway_actually_wrote(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    """The written hint travels home, because only this layer knows what it resolved to.

    ``hint=None`` means KEEP and is resolved against the gateway's OWN fresh read, so
    the service's separate live read can legitimately disagree with the wire — and
    reporting THAT let the response name a hint the account does not have.
    """
    client = RawClient(password=Password(has_password=True, hint="the usual"))
    patch_action_client(monkeypatch, client)

    result = await execute("acc-2fa", SetTwoFactorPassword(new_password=PASSWORD))

    assert result.twofa_hint == "the usual"
    assert client.written().new_settings.hint == "the usual"


def test_neither_password_appears_in_the_actions_repr() -> None:
    """Both secrets carry ``repr=False``, so neither rides a rendered frame local.

    Pydantic renders every field into ``__repr__``, and ``repr()`` is exactly what an
    error tracker ships out of a stack frame's locals.
    """
    rendered = repr(
        SetTwoFactorPassword(current_password="OLD-SECRET", new_password=PASSWORD, hint="h"),
    )

    assert "OLD-SECRET" not in rendered
    assert PASSWORD not in rendered
    # The public half is still legible, so the repr stays useful for debugging.
    assert "hint='h'" in rendered


def test_both_passwords_none_is_refused_by_the_action() -> None:
    """That combination names no verb, so the request would mean nothing at all."""
    with pytest.raises(ValueError, match="current_password/new_password"):
        SetTwoFactorPassword()


def test_an_empty_new_password_is_refused_by_the_action() -> None:
    """``""`` is not the removal verb — ``None`` is — so it must not be constructible.

    ``_new_password_hash`` branches on ``is None``, so an empty string would be
    HASHED like a real password rather than removing anything, and the docstring's
    own invariant is about exactly that value. The API layer's ``min_length=8``
    keeps it off the HTTP path, but this boundary has to hold for any caller.
    """
    with pytest.raises(ValueError, match="new_password"):
        SetTwoFactorPassword(new_password="")
