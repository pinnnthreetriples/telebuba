"""Cloud-password (2FA) dispatch — ``account.getPassword`` / ``client.edit_2fa``.

Extracted-sibling pattern (see ``_privacy.py``): ``_read.py`` keeps the read
match and ``_actions.py`` the write one, both importing from here. Errors ride
the existing ladders untouched apart from the small map below — a read's
FloodWait/RPCError becomes ``TelegramReadError`` in ``execute_read_many``, a
write's is classified by ``execute`` — so there is no retry logic in this module.

**The action carries a plaintext secret.** Nothing here may put
``current_password`` / ``new_password`` into an exception message, a log extra or
a ``str(exc)``. Every refusal is reported as a bounded code from
``_TWOFA_ERROR_CODES``, and ``tests/core/telegram_client/test_twofa.py`` asserts
that no dispatched request or raised message contains the password.

Three pieces of ``edit_2fa`` behaviour this module has to encode, all read off
``telethon/client/auth.py`` (1.44) rather than inferred:

- The field pair IS the verb. ``new_password`` alone sets, both change,
  ``current_password`` alone removes. Both ``None`` returns ``False`` without
  any RPC, which is why ``SetTwoFactorPassword`` refuses that combination.
- ``if not pwd.has_password and current_password: current_password = None`` —
  Telethon silently drops a supplied current password when 2FA is off, so a
  "remove" against an account without 2FA degrades to both-``None`` and returns
  ``False``. A ``False`` return is therefore a no-op, never a success, and is
  raised as ``twofa_not_changed`` instead of being reported as one.
- Sending only ``new_password`` while 2FA is already ON makes Telethon send
  ``InputCheckPasswordEmpty``, and Telegram answers ``PasswordHashInvalidError``.
  That is the safety net behind the service-layer precondition, surfaced as a
  clean "wrong current password" rather than Telethon's English prose.

The recovery email deliberately does NOT go through ``edit_2fa``, and this is
the one fact most likely to be "simplified" back into a password wipe:

- ``edit_2fa`` sets ``new_password_hash = b''`` whenever ``new_password`` is
  falsy, and an empty hash REMOVES the cloud password. So calling it with only an
  email would attach the address and delete the password in the same request.
- ``edit_2fa``'s ``email`` argument also demands an ``email_code_callback`` that
  reads the confirmation code out of a mailbox, which an unattended backend
  cannot do. The operator reads their own letter and types the code into a second
  request instead, which is why ``confirm`` is a mode and not a callback.

So the email path issues the raw ``account.updatePasswordSettings`` with
``new_algo`` and ``new_password_hash`` OMITTED from the flags and only ``email``
set — what TDLib's ``PasswordManager::set_recovery_email_address`` does
(``update_password=false``, ``update_recovery_email_address=true``) — and
authorises it with ``telethon.password.compute_check``.
``tests/core/telegram_client/test_twofa_email.py`` asserts both fields are
``None`` on the wire, because that assertion is what stands between this feature
and a silent password wipe.

``clear`` rides that exact call with ``email=""``. An empty STRING is still a
present flag (Telethon omits a field only when it is ``None`` or ``False``), which
is how a confirmed recovery address is detached; ``cancelPasswordEmail`` cannot do
it, because that RPC only abandons a verification still in flight. The password
fields stay ``None`` here as well — the same assertion covers both modes.

``EMAIL_UNCONFIRMED_<N>`` is the SUCCESS signal of that call, not a failure: the
address is attached and a code of length ``N`` has just been mailed. TDLib
resolves its promise with ``true`` on it and merely extracts ``N``. Treating it
as an error would strand the operator with a pending email they cannot act on, so
it is caught and returned as ``pending=True``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors
from telethon.password import compute_check
from telethon.tl.functions.account import (
    CancelPasswordEmailRequest,
    ConfirmPasswordEmailRequest,
    GetPasswordRequest,
    ResendPasswordEmailRequest,
    UpdatePasswordSettingsRequest,
)
from telethon.tl.types.account import PasswordInputSettings

from core.telegram_client._action_results import _DispatchResult
from schemas.telegram_actions_twofa import (
    ManageTwoFactorEmail,
    SetTwoFactorPassword,
    TwoFactorStatusResult,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

# Telethon refusal family → stable, locale-neutral code (mirrors
# ``_profile._PROFILE_ERROR_CODES``). The three SRP members all mean the same
# operator-visible thing: the current password we sent did not authorise the
# change — either it is wrong, or the SRP challenge we computed against went
# stale because the password changed elsewhere mid-call. Flood-family errors are
# deliberately NOT mapped: they must reach ``execute``'s dedicated flood-wait
# ladder unchanged.
_TWOFA_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (errors.PasswordHashInvalidError, "twofa_current_password_invalid"),
    (errors.SrpIdInvalidError, "twofa_current_password_invalid"),
    (errors.SrpPasswordChangedError, "twofa_current_password_invalid"),
    (errors.NewSettingsInvalidError, "twofa_settings_invalid"),
    # The recovery-email half. ``EmailInvalidError`` is mapped even though the API
    # layer already refuses an address with no ``@``: that check is deliberately
    # weak (no ``email-validator`` dependency), so Telegram is the real validator
    # here and its refusal has to be legible rather than an opaque ``failed``.
    (errors.CodeInvalidError, "twofa_email_code_invalid"),
    (errors.EmailHashExpiredError, "twofa_email_hash_expired"),
    (errors.EmailInvalidError, "twofa_email_invalid"),
)


class TwoFactorGatewayError(ValueError):
    """A cloud-password action was refused; ``str(exc)`` is the stable code.

    Same contract as :class:`core.telegram_client._media.ProfileGatewayError`:
    the code rides ``execute``'s generic-exception ladder into
    ``ActionResult.error_message`` verbatim and the SPA translates it. The
    unreadable detail travels as the chained cause into the failure log — and
    for this family that chained cause is the only place any Telethon text about
    the attempt exists, since the code carries no password and no prose.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _flag(source: object, name: str) -> bool:
    """One ``account.getPassword`` boolean, defaulting to "no" when unset.

    Every flag in ``account.Password`` is optional in the TL schema (an unset
    flag arrives as ``None``), and a test double answers every attribute with
    another mock. Neither is a "yes", so both collapse to ``False`` — the same
    defensive ``getattr`` idiom ``_privacy._level_from_rules`` uses.
    """
    value = getattr(source, name, None)
    return value is True


def _text(source: object, name: str) -> str | None:
    """One optional string field, or ``None`` when nothing answered it."""
    value = getattr(source, name, None)
    return value if isinstance(value, str) else None


def _gateway_error(exc: errors.RPCError) -> BaseException:
    """The mapped :class:`TwoFactorGatewayError`, or ``exc`` itself when unmapped.

    Returning rather than raising keeps the ``raise ... from exc`` chain at the
    call site, so an unmapped refusal reaches ``execute``'s generic ladder with
    its own class intact — the flood family included, which must never be mapped.
    """
    for error_cls, code in _TWOFA_ERROR_CODES:
        if isinstance(exc, error_cls):
            return TwoFactorGatewayError(code)
    return exc


async def dispatch_get_twofa_status(client: TelegramClient) -> TwoFactorStatusResult:
    """Read the live 2FA state — one ``account.getPassword``, nothing cached.

    ``pending_reset_date`` arrives as a ``datetime`` and is rendered with
    ``.isoformat()`` because every timestamp crossing this boundary is an ISO
    string. Anything that is not a ``datetime`` (an unset flag, a mock) is
    reported as absent rather than stringified into a value the UI would parse.
    """
    pwd = await client(GetPasswordRequest())
    reset_at = getattr(pwd, "pending_reset_date", None)
    return TwoFactorStatusResult(
        has_password=_flag(pwd, "has_password"),
        hint=_text(pwd, "hint"),
        has_recovery=_flag(pwd, "has_recovery"),
        pending_reset_date=reset_at.isoformat() if hasattr(reset_at, "isoformat") else None,
        email_unconfirmed_pattern=_text(pwd, "email_unconfirmed_pattern"),
    )


async def dispatch_set_twofa_password(
    client: TelegramClient,
    action: SetTwoFactorPassword,
) -> None:
    """Set / change / remove the cloud password — see the module docstring for which.

    ponytail: known ceiling, deliberately not addressed. ``edit_2fa`` runs
    Telethon's ``compute_digest`` in pure Python — 2048-bit modular exponentiation
    plus a primality check — which blocks the event loop for up to ~1s per call.
    Acceptable for a single-account operator action; offload it to a thread if
    this ever grows a fleet-wide sweep.
    """
    try:
        changed = await client.edit_2fa(
            # Telethon annotates both as ``str`` while defaulting them to ``None``,
            # and ``None`` is how the three verbs are spelled (see the docstring).
            current_password=action.current_password,  # ty: ignore[invalid-argument-type]
            new_password=action.new_password,  # ty: ignore[invalid-argument-type]
            hint=action.hint,
        )
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    if not changed:
        code = "twofa_not_changed"
        raise TwoFactorGatewayError(code)


async def _write_recovery_email(
    client: TelegramClient,
    action: ManageTwoFactorEmail,
) -> int | None:
    """``account.updatePasswordSettings`` with the password fields left OUT.

    Serves both ``set`` and ``clear``: the only difference is the address, and
    ``clear`` sends an empty one. ``PasswordInputSettings`` carries ``email`` and
    nothing else — ``new_algo`` and ``new_password_hash`` stay ``None`` so Telethon
    omits them from the TL flags, which is what makes this an email-only change; an
    empty-bytes hash there would delete the cloud password instead (see the module
    docstring).

    ``compute_check`` needs the CURRENT algorithm off a fresh ``getPassword``, so
    the read is part of this call and not cached. It raises a bare ``ValueError``
    when ``current_algo`` is absent, which is exactly the "2FA is off" case — a
    recovery email has nothing to guard then, so that is refused with a stable
    code instead of Telethon's prose.

    Answers the length of the code Telegram just mailed, or ``None`` when it asked
    for no confirmation.
    """
    pwd = await client(GetPasswordRequest())
    if getattr(pwd, "current_algo", None) is None:
        code = "twofa_password_not_set"
        raise TwoFactorGatewayError(code)
    # ``clear`` sends an EMPTY address, which is what detaches a confirmed one.
    email = action.email if action.mode == "set" else ""
    try:
        await client(
            UpdatePasswordSettingsRequest(
                password=compute_check(pwd, action.current_password),  # ty: ignore[invalid-argument-type]
                new_settings=PasswordInputSettings(email=email),
            ),
        )
    except errors.EmailUnconfirmedError as exc:
        # The happy path, not a failure: the address is attached and a code of
        # this length was just mailed to it.
        return exc.code_length
    return None


async def dispatch_manage_twofa_email(
    client: TelegramClient,
    action: ManageTwoFactorEmail,
) -> int | None:
    """One dispatcher for all five recovery-email modes; answers the code length.

    Only ``set`` can learn a length — it is the sole mode Telegram answers with
    ``EMAIL_UNCONFIRMED_<N>``. ``resend`` mails another code but replies with a bare
    ``Bool`` that never repeats the length, so reporting the previous one would be a
    guess; every other mode has no code to type at all.
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
    return None


async def dispatch_twofa_action(
    client: TelegramClient,
    action: SetTwoFactorPassword | ManageTwoFactorEmail,
) -> _DispatchResult:
    """The executor's single entry point for both 2FA write actions.

    One entry point for two actions on purpose: every match arm in
    ``_actions._dispatch_action`` costs cyclomatic complexity, and that function
    sits exactly at ``tools/radon_gate.py``'s ceiling of 20. Branching here costs
    the gate nothing.

    ``code_length`` rides home on ``_DispatchResult`` because there is no other
    way for the service to learn it: it exists only inside the
    ``EMAIL_UNCONFIRMED_<N>`` this module just swallowed, and the operator cannot
    type the code without it.
    """
    if isinstance(action, SetTwoFactorPassword):
        await dispatch_set_twofa_password(client, action)
        return _DispatchResult()
    code_length = await dispatch_manage_twofa_email(client, action)
    return _DispatchResult(twofa_email_code_length=code_length)


def twofa_log_extra(action: SetTwoFactorPassword | ManageTwoFactorEmail) -> dict[str, object]:
    """Log fields for both 2FA writes — booleans and the mode, never a secret.

    Every field these two actions carry is either a password, a recovery email or
    a mailed confirmation code, so the log records what KIND of write happened and
    nothing about its payload. Lives here rather than in ``_action_log_extra`` so
    the two actions share one arm there.
    """
    if isinstance(action, SetTwoFactorPassword):
        return {"has_hint": bool(action.hint), "removing": action.new_password is None}
    return {"mode": action.mode}
