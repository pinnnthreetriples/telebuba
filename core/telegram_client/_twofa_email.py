"""Recovery-email dispatch — ``account.updatePasswordSettings`` and its four friends.

Extracted sibling of ``_twofa`` (440-line file budget), which keeps the refusal
ladder, :class:`TwoFactorGatewayError`, the bounded :func:`_srp` worker and the
password half. The dependency runs one way at module scope — this module imports
those four names — and ``_twofa.dispatch_twofa_action`` imports back from here
INSIDE the function for that reason.

The secret discipline is the same rule one file over: the stored password, the
recovery address and the code Telegram mails may not reach an exception message,
a log extra or a ``str(exc)``.

The EMAIL half omits the password fields entirely, and that is the fact most
likely to be "simplified" into a password wipe: an empty ``new_password_hash``
does not mean "leave the password alone", it means DELETE it. So that call sends
only ``email`` — what TDLib's ``PasswordManager::set_recovery_email_address``
does. ``test_twofa_email.py`` asserts both fields are ``None`` on the wire and
``test_twofa.py`` asserts the same wire for the three password verbs, where
exactly one may carry the empty hash; those two assertions are what stand between
this feature and a silent password wipe.

``edit_2fa``'s ``email`` would also demand an ``email_code_callback`` reading the
code out of a mailbox, which an unattended backend cannot do — so the operator
types the code into a second request and ``confirm`` is a mode, not a callback.

``clear`` rides that exact call with ``email=""``: an empty STRING is still a
present flag (Telethon omits a field only when it is ``None`` or ``False``), which
is how a CONFIRMED address is detached — ``cancelPasswordEmail`` only abandons a
verification still in flight.

``EMAIL_UNCONFIRMED_<N>`` is the SUCCESS signal of that call, not a failure: the
address is attached and a code of length ``N`` was just mailed, which is how TDLib
reads it too, so it is reported as ``twofa_email_unconfirmed`` rather than
stranding the operator with a pending email they cannot act on. What the PASSWORD
path does with the same answer is decided by a live re-read rather than assumed —
see ``_twofa._email_unconfirmed_result``, which also records why the TDLib member
an earlier round cited here does not exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.password import compute_check
from telethon.tl.functions.account import (
    CancelPasswordEmailRequest,
    ConfirmPasswordEmailRequest,
    ResendPasswordEmailRequest,
    UpdatePasswordSettingsRequest,
)
from telethon.tl.types.account import PasswordInputSettings

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._twofa import _gateway_error, _password_state
from core.telegram_client._twofa_srp import TwoFactorGatewayError, _srp, require_fast_algo

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions_twofa import ManageTwoFactorEmail


async def _write_recovery_email(
    client: TelegramClient,
    action: ManageTwoFactorEmail,
) -> _DispatchResult:
    """``account.updatePasswordSettings`` with the password fields left OUT.

    Serves both ``set`` and ``clear``; the only difference is the address, and
    ``clear`` sends an empty one. ``new_algo`` and ``new_password_hash`` stay ``None``
    so Telethon omits them from the TL flags, which is what makes this an email-only
    change — an empty-bytes hash there would DELETE the cloud password instead.

    ``compute_check`` needs the CURRENT algorithm off a fresh ``getPassword``, so
    the read is part of this call and not cached — and that read goes through
    ``_twofa._password_state`` like the password half's does, because a socket dying
    on it PROVES nothing was sent: left bare, ``execute`` stamped it
    ``UNCONFIRMED_ERROR_TYPE`` and told the operator Telegram may have applied a
    request that never left the process. A missing ``current_algo`` is the "2FA is
    off" case, which a recovery email has nothing to guard, so it is refused with a
    stable code rather than Telethon's ``AttributeError``; a ``(p, g)`` outside
    Telethon's fast path is refused by ``require_fast_algo`` before the proof is
    offloaded at all, for the reason ``_twofa_srp`` documents.

    Reports whether Telegram answered ``EMAIL_UNCONFIRMED`` and, separately, the
    length of the code it mailed. The bare form of that error has no ``_<N>`` suffix
    and Telethon maps it to ``code_length = 0``; zero is not a length — it reaches
    the card as ``maxLength={0}``, an input nobody can type into next to a Confirm
    button that can never enable — so the length is ``None`` while the FLAG still
    says pending. Deriving one from the other made a pending address look verified.
    """
    pwd = await _password_state(client)
    current_algo = getattr(pwd, "current_algo", None)
    if current_algo is None:
        code = "twofa_password_not_set"
        raise TwoFactorGatewayError(code)
    require_fast_algo(current_algo)
    # ``clear`` sends an EMPTY address, which is what detaches a confirmed one.
    email = action.email if action.mode == "set" else ""
    proof = await _srp(compute_check, pwd, action.current_password)
    try:
        await client(
            UpdatePasswordSettingsRequest(
                password=proof,
                new_settings=PasswordInputSettings(email=email),
            ),
        )
    except errors.EmailUnconfirmedError as exc:
        # The happy path, not a failure: the address is attached and a code was just
        # mailed to it. The FLAG is what says so; the length rides along only when
        # Telegram supplied one, and callers must not reconstruct the flag from it.
        return _DispatchResult(
            twofa_email_unconfirmed=True,
            twofa_email_code_length=exc.code_length or None,
        )
    return _DispatchResult()


async def dispatch_manage_twofa_email(
    client: TelegramClient,
    action: ManageTwoFactorEmail,
) -> _DispatchResult:
    """One dispatcher for all five recovery-email modes; answers what it learned.

    Only ``set`` can learn a length — it is the sole mode Telegram answers with
    ``EMAIL_UNCONFIRMED_<N>``. ``resend`` mails another code but replies with a bare
    ``Bool``, so repeating the previous length would be a guess.
    """
    try:
        if action.mode in {"set", "clear"}:
            return await _write_recovery_email(client, action)
        if action.mode == "confirm":
            await client(ConfirmPasswordEmailRequest(code=action.code))  # ty: ignore[invalid-argument-type]
        elif action.mode == "resend":
            await client(ResendPasswordEmailRequest())
        else:
            await client(CancelPasswordEmailRequest())
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    return _DispatchResult()
