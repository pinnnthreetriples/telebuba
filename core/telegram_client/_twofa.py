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
# ladder unchanged. Neither may anything that carries ``.seconds`` — mapping it here
# would flatten the duration into a bare code, which is why ``SESSION_TOO_FRESH`` and
# ``PASSWORD_TOO_FRESH`` are absent from this table and handled by that ladder
# instead, even though both are ``BadRequestError`` rather than floods.
_TWOFA_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (errors.PasswordHashInvalidError, "twofa_current_password_invalid"),
    (errors.SrpIdInvalidError, "twofa_current_password_invalid"),
    (errors.SrpPasswordChangedError, "twofa_current_password_invalid"),
    (errors.NewSettingsInvalidError, "twofa_settings_invalid"),
    # ``NEW_SALT_INVALID`` is the same refusal one field over: Telegram rejected the
    # settings we computed, and ``edit_2fa`` is what builds that salt
    # (``pwd.new_algo.salt1 += os.urandom(32)``), so there is nothing for the
    # operator to correct beyond retrying — exactly what the settings copy says.
    (errors.NewSaltInvalidError, "twofa_settings_invalid"),
    # ``PASSWORD_MISSING`` means 2FA is not enabled on the account, which is the
    # likely answer to a confirm / resend / cancel fired with nothing pending, and to
    # any authorised write against an account whose password went away elsewhere. It
    # is the same fact the ``current_algo`` guard below raises by hand.
    (errors.PasswordMissingError, "twofa_password_not_set"),
    # The recovery-email half. ``EmailInvalidError`` is mapped even though the API
    # layer already refuses an address with no ``@``: that check is deliberately
    # weak (no ``email-validator`` dependency), so Telegram is the real validator
    # here and its refusal has to be legible rather than an opaque ``failed``.
    (errors.CodeInvalidError, "twofa_email_code_invalid"),
    (errors.EmailHashExpiredError, "twofa_email_hash_expired"),
    # ``EMAIL_VERIFY_EXPIRED`` is the same window closing, reported under a second
    # name; one code, because "attach the address again" is the one way out of both.
    (errors.EmailVerifyExpiredError, "twofa_email_hash_expired"),
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

    The PRE-FLIGHT read is what makes "the answer was lost" honest. ``edit_2fa``
    issues its own ``account.getPassword`` before it writes anything, so a socket
    dying on that first leg used to be classified from ``client is not None`` alone
    and reported as "Telegram may have applied this password" — for a request that
    provably never left. Reading the state here first moves that whole window into a
    plain failure with a stable code, and only a fault AFTER this succeeded can still
    mean the write left the process. The read costs one extra RPC and buys the
    difference between "may be live" and "is not".

    It also answers the hint. ``updatePasswordSettings`` always writes the field, so
    ``hint=None`` (keep) is resolved against this read; only ``""`` clears.

    ponytail: known ceiling, deliberately not addressed. ``edit_2fa`` runs
    Telethon's ``compute_digest`` in pure Python — 2048-bit modular exponentiation
    plus a primality check — which blocks the event loop for up to ~1s per call.
    Acceptable for a single-account operator action; offload it to a thread if
    this ever grows a fleet-wide sweep.
    """
    pwd = await _preflight_password(client)
    try:
        changed = await client.edit_2fa(
            # Telethon annotates both as ``str`` while defaulting them to ``None``,
            # and ``None`` is how the three verbs are spelled (see the docstring).
            current_password=action.current_password,  # ty: ignore[invalid-argument-type]
            new_password=action.new_password,  # ty: ignore[invalid-argument-type]
            hint=action.hint if action.hint is not None else _text(pwd, "hint") or "",
        )
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    except TypeError as exc:
        # ``EMAIL_UNCONFIRMED`` while a recovery-email verification is still pending.
        # Telethon answers it INSIDE its own except clause by calling
        # ``email_code_callback(e.code_length)`` — and this path passes no callback,
        # because an unattended backend cannot read a mailbox. So what escapes is not
        # the Telegram error but the ``'NoneType' object is not callable`` raised while
        # handling it, which is why the context is what has to be inspected. The write
        # itself was ACCEPTED: this is the same server answer ``_write_recovery_email``
        # treats as success, and the two paths must not disagree about it. The pending
        # email is untouched — no ``email`` was sent.
        if not isinstance(exc.__context__, errors.EmailUnconfirmedError):
            raise
        return
    if not changed:
        code = "twofa_not_changed"
        raise TwoFactorGatewayError(code)


async def _preflight_password(client: TelegramClient) -> object:
    """``account.getPassword`` before any write, so a lost read is not a lost write.

    A transport failure here becomes an ordinary refusal with a stable code rather
    than reaching ``execute``'s ``unavailable`` arm, which would mark it
    ``UNCONFIRMED_ERROR_TYPE`` — "Telegram may have applied it" — for a call that had
    not yet sent anything. Only ``ConnectionError`` / ``TimeoutError`` are caught,
    because those are exactly the classes that arm keys off; everything else keeps
    its own ladder.
    """
    try:
        return await client(GetPasswordRequest())
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    except (ConnectionError, TimeoutError) as exc:
        code = "twofa_state_unreadable"
        raise TwoFactorGatewayError(code) from exc


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
    for no confirmation — and also ``None`` for the bare ``EMAIL_UNCONFIRMED`` with no
    ``_<N>`` suffix, which Telethon maps to the same class with ``code_length = 0``.
    Zero is not a length: reported verbatim it reaches the card as
    ``maxLength={0}``, an input nobody can type into and a Confirm button that can
    never enable. "Telegram did not say" is the honest answer, and the pending
    address still arrives on the next status read as ``email_unconfirmed_pattern``.
    """
    pwd = await client(GetPasswordRequest())
    if getattr(pwd, "current_algo", None) is None:
        code = "twofa_password_not_set"
        raise TwoFactorGatewayError(code)
    # ``clear`` sends an EMPTY address, which is what detaches a confirmed one.
    email = action.email if action.mode == "set" else ""
    # Bare ``ValueError`` is ``compute_check``'s whole vocabulary for a challenge it
    # cannot use — an algorithm class it does not implement, a bad p/g, a bad B or
    # g_b. None of it is actionable prose (and all of it names Telethon internals),
    # so it collapses into one stable code instead of an opaque ``failed``.
    try:
        proof = compute_check(pwd, action.current_password)  # ty: ignore[invalid-argument-type]
    except ValueError as exc:
        code = "twofa_password_algo_unsupported"
        raise TwoFactorGatewayError(code) from exc
    try:
        await client(
            UpdatePasswordSettingsRequest(
                password=proof,
                new_settings=PasswordInputSettings(email=email),
            ),
        )
    except errors.EmailUnconfirmedError as exc:
        # The happy path, not a failure: the address is attached and a code of
        # this length was just mailed to it.
        return exc.code_length or None
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
