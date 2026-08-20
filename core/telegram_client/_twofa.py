"""Cloud-password (2FA) dispatch — ``account.getPassword`` / ``updatePasswordSettings``.

Extracted-sibling pattern (see ``_privacy.py``): ``_read.py`` keeps the read match
and ``_actions.py`` the write one, both importing from here. Errors ride the
existing ladders apart from the small map below, so there is no retry logic here.

**The action carries a plaintext secret.** Nothing here may put
``current_password`` / ``new_password`` into an exception message, a log extra or
a ``str(exc)``. Every refusal is a bounded code from ``_TWOFA_ERROR_CODES``, and
``test_twofa.py`` asserts no dispatched request or message contains the password.

BOTH halves issue the raw ``account.updatePasswordSettings``; ``client.edit_2fa``
is deliberately not used by either, for different reasons per half.

The PASSWORD half reproduces ``edit_2fa``'s body (telethon 1.44
``client/auth.py``) minus the ``email`` / ``email_code_callback`` arm an
unattended backend cannot use. Delegating cost four things:

- the SRP work ran on the event-loop thread, and it can fail to terminate at all
  (:func:`_srp`, which owns both the offload and the bound);
- ``edit_2fa`` issued its OWN ``getPassword``, so a pre-flight read here could
  not make a dead read honest — the socket just died one leg later, and it was
  still reported as "Telegram may have applied this password"
  (:func:`_password_state`);
- ``EMAIL_UNCONFIRMED`` arrived as the ``'NoneType' object is not callable``
  ``TypeError`` raised while ``edit_2fa`` called the callback we cannot pass;
- ``new_password_hash = b''`` was its default for every falsy ``new_password``,
  making the field that DELETES a password a default rather than a decision
  (:func:`_new_password_hash`).

Two ``edit_2fa`` behaviours it keeps on purpose, read off that source rather
than inferred: the field pair IS the verb (``SetTwoFactorPassword`` documents
which combination means what, and refuses the one that means nothing), and a
supplied current password is DROPPED when 2FA is off, because there is nothing
to check it against — so a "remove" then removes nothing, which is a no-op and
never a success.

The recovery-email half lives in the extracted sibling ``_twofa_email``, which
imports the refusal ladder and :func:`_srp` from here and is imported back only
inside :func:`dispatch_twofa_action` — see the comment there.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from telethon import errors
from telethon.password import compute_check, compute_digest
from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
from telethon.tl.types import (
    InputCheckPasswordEmpty,
    PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow,
)
from telethon.tl.types.account import Password, PasswordInputSettings

from core.telegram_client._action_results import _DispatchResult
from schemas.telegram_actions_twofa import (
    ManageTwoFactorEmail,
    SetTwoFactorPassword,
    TwoFactorStatusResult,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from telethon import TelegramClient

# The one KDF class Telethon implements. ``passwordKdfAlgoUnknown`` is the other member
# of that TL union and carries no salt at all, so it is refused rather than reached.
_ModPowAlgo = PasswordKdfAlgoSHA256SHA256PBKDF2HMACSHA512iter100000SHA256ModPow

# Telethon refusal family → stable, locale-neutral code (mirrors
# ``_profile._PROFILE_ERROR_CODES``). The three SRP members all mean one
# operator-visible thing: the current password we sent did not authorise the change —
# it is wrong, or the challenge we computed against went stale mid-call. Nothing
# carrying ``.seconds`` may be mapped here, because a bare code flattens the
# duration: that is why the flood family and ``SESSION_TOO_FRESH`` /
# ``PASSWORD_TOO_FRESH`` are absent and handled by ``execute``'s wait ladder, even
# though the last two are ``BadRequestError`` rather than floods.
_TWOFA_ERROR_CODES: tuple[tuple[type[Exception], str], ...] = (
    (errors.PasswordHashInvalidError, "twofa_current_password_invalid"),
    (errors.SrpIdInvalidError, "twofa_current_password_invalid"),
    (errors.SrpPasswordChangedError, "twofa_current_password_invalid"),
    (errors.NewSettingsInvalidError, "twofa_settings_invalid"),
    # ``NEW_SALT_INVALID`` is the same refusal one field over: the salt is ours to
    # build (``pwd.new_algo.salt1 += os.urandom(32)``), so there is nothing for the
    # operator to correct beyond retrying — exactly what the settings copy says.
    (errors.NewSaltInvalidError, "twofa_settings_invalid"),
    # ``PASSWORD_MISSING`` means 2FA is not enabled: the likely answer to a confirm /
    # resend / cancel fired with nothing pending, and to any authorised write against
    # an account whose password went away elsewhere. Same fact as the guards below.
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

    Same contract as :class:`core.telegram_client._media.ProfileGatewayError`: the
    code rides ``execute``'s generic-exception ladder into
    ``ActionResult.error_message`` verbatim and the SPA translates it. The unreadable
    detail travels as the chained cause into the failure log — for this family the
    only place any Telethon text about the attempt exists.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# Pure-Python 2048-bit modular arithmetic, MEASURED: 68 ms for a ``compute_digest``,
# 98 ms for a ``compute_check``, 165 ms for a change doing both — during which a
# heartbeat on the loop thread came back 171 ms late. (An earlier comment here
# guessed "~1s"; these are the numbers.) That is why it runs in a thread.
#
# The BOUND is a different problem and is not about milliseconds. Both functions call
# ``telethon.password.check_prime_and_good``, whose fast path fires only when
# ``algo.p`` byte-equals Telethon's hardcoded prime; any other ``p`` falls into
# Pollard-Brent factorisation of a prime, which does not terminate — measured on the
# RFC 3526 group-14 prime, still running after 30 s. ``p`` is SERVER-supplied, so a
# Telegram prime rotation would otherwise wedge the single uvicorn worker for good,
# taking warming, the listener, SSE and ``/ready`` with it.
#
# What the bound buys is one failed request instead of a dead process. It does NOT
# stop the spinning: Python cannot kill a thread, so every expiry LEAKS one worker
# thread burning a core until the process exits. That is the trade, deliberately.
_SRP_TIMEOUT_SECONDS = 15.0


async def _srp[T](compute: Callable[..., T], *args: object) -> T:
    """One SRP computation, off the loop thread and bounded — see the comment above.

    Both refusals collapse into stable codes here rather than at the call sites. A
    bare ``ValueError`` is ``compute_check`` / ``compute_digest``'s whole vocabulary
    for a challenge they cannot use (an unimplemented algorithm class, a bad p/g, a
    bad B or g_b), none of it actionable prose. Doing it here also keeps the two
    apart: ``TwoFactorGatewayError`` IS a ``ValueError``, so a call site wrapping
    this in ``except ValueError`` would relabel the timeout as a bad algorithm.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(compute, *args), _SRP_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        code = "twofa_password_compute_timeout"
        raise TwoFactorGatewayError(code) from exc
    except ValueError as exc:
        code = "twofa_password_algo_unsupported"
        raise TwoFactorGatewayError(code) from exc


def _flag(source: object, name: str) -> bool:
    """One ``account.getPassword`` boolean, defaulting to "no" when unset.

    Every flag in ``account.Password`` is optional in the TL schema (an unset flag
    arrives as ``None``), and a mock answers every attribute with another mock.
    Neither is a "yes", so both collapse to ``False`` — the defensive ``getattr``
    idiom ``_privacy._level_from_rules`` uses.
    """
    value = getattr(source, name, None)
    return value is True


def _text(source: object, name: str) -> str | None:
    """One optional string field, or ``None`` when nothing answered it."""
    value = getattr(source, name, None)
    return value if isinstance(value, str) else None


def _gateway_error(exc: errors.RPCError) -> BaseException:
    """The mapped :class:`TwoFactorGatewayError`, or ``exc`` itself when unmapped.

    Returning rather than raising keeps the ``raise ... from exc`` chain at the call
    site, so an unmapped refusal reaches ``execute``'s generic ladder with its own
    class intact — the flood family included, which must never be mapped.
    """
    for error_cls, code in _TWOFA_ERROR_CODES:
        if isinstance(exc, error_cls):
            return TwoFactorGatewayError(code)
    return exc


async def dispatch_get_twofa_status(client: TelegramClient) -> TwoFactorStatusResult:
    """Read the live 2FA state — one ``account.getPassword``, nothing cached.

    ``pending_reset_date`` arrives as a ``datetime`` and is rendered with
    ``.isoformat()`` because every timestamp crossing this boundary is an ISO string.
    Anything else (an unset flag, a mock) is reported as absent, never stringified.
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
) -> _DispatchResult:
    """Set / change / remove the cloud password: ONE ``getPassword``, ONE write.

    ``edit_2fa``'s body, reproduced — see the module docstring for the four reasons
    it is not called. The SRP work is the only part that costs measurable CPU, which
    is why both halves of it go through :func:`_srp` instead of being awaited here.
    """
    pwd = await _password_state(client)
    current_password = action.current_password
    if not _flag(pwd, "has_password"):
        if action.new_password is None:
            # With no password on the account there is nothing for a current one to
            # authorise, so a REMOVAL removes nothing. ``edit_2fa`` answered that with
            # ``False`` from a call it never sent: a no-op, never a success. The
            # service's stale branch is what actually resolves this state.
            code = "twofa_not_changed"
            raise TwoFactorGatewayError(code)
        current_password = None
    algo = pwd.new_algo
    if not isinstance(algo, _ModPowAlgo):
        # ``passwordKdfAlgoUnknown``: the server offered a KDF this Telethon cannot
        # implement, so there is no salt to extend and no digest to compute. Telethon
        # would ``AttributeError`` on the next line; this is the same stable code
        # ``compute_digest``'s own ``ValueError`` would have produced.
        code = "twofa_password_algo_unsupported"
        raise TwoFactorGatewayError(code)
    # Telethon's line, security-relevant rather than cosmetic: the SERVER chose
    # ``salt1``, and 32 bytes of client randomness are appended so the KDF is not
    # keyed by a salt it alone controls. Dropping this silently weakens every
    # password this dashboard sets; ``NEW_SALT_INVALID`` is its refusal.
    algo.salt1 += os.urandom(32)
    proof = (
        await _srp(compute_check, pwd, current_password)
        if current_password is not None
        else InputCheckPasswordEmpty()
    )
    try:
        await client(
            UpdatePasswordSettingsRequest(
                password=proof,
                new_settings=PasswordInputSettings(
                    new_algo=algo,
                    new_password_hash=await _new_password_hash(algo, action),
                    hint=_resolved_hint(pwd, action),
                ),
            ),
        )
    except errors.EmailUnconfirmedError:
        # ACCEPTED, but not plainly done: a recovery-email verification is still
        # pending and TDLib holds its ``last_set_password_`` until the mailed code is
        # typed back, so the new password is not certainly in force. Applied-but-
        # unconfirmed is therefore the honest report, never a clean success.
        return _DispatchResult(twofa_email_unconfirmed=True)
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    return _DispatchResult()


async def _password_state(client: TelegramClient) -> Password:
    """The ONE ``account.getPassword`` a password write makes.

    Typed as ``account.Password`` even though every field on it is an optional TL
    flag — which is why the two readers go through ``_flag`` / ``_text`` — because
    ``new_algo`` is the one attribute this path requires outright.

    It is the first thing the write does, so a dead socket here PROVES nothing was
    sent. Left to escape it would reach ``execute``'s ``dispatched=client is not
    None`` arm as ``UNCONFIRMED_ERROR_TYPE`` — "Telegram may have applied this
    password" — for a request that never left the process, and the service persists
    an unconfirmed password on the strength of that. Only ``ConnectionError`` /
    ``TimeoutError`` are caught: exactly the classes that arm keys off.
    """
    try:
        return await client(GetPasswordRequest())
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    except (ConnectionError, TimeoutError) as exc:
        code = "twofa_state_unreadable"
        raise TwoFactorGatewayError(code) from exc


async def _new_password_hash(algo: _ModPowAlgo, action: SetTwoFactorPassword) -> bytes:
    """The new password's SRP digest, or the ONE deliberate empty hash in this module.

    A present-but-EMPTY ``new_password_hash`` is not "no change", it is a DELETION —
    which is why the recovery-email path omits the field entirely. So it is written
    here and nowhere else, from the verb (``new_password is None``) rather than from
    a falsy check: the difference between a decision and ``edit_2fa``'s default.
    """
    if action.new_password is None:
        return b""
    return await _srp(compute_digest, algo, action.new_password)


def _resolved_hint(pwd: object, action: SetTwoFactorPassword) -> str:
    """The hint to WRITE. Only a set or a change may carry one.

    ``updatePasswordSettings`` always writes the field, so ``hint=None`` has to mean
    KEEP and is resolved against the read above; ``""`` is the deliberate clear.

    A REMOVAL resolves nothing. Its action carries ``hint=None`` too — the service
    builds it from ``current_password`` alone — so resolving would ship the live hint
    alongside the empty hash: a combination never sent before ``None`` came to mean
    "keep", and one Telegram may refuse as ``NEW_SETTINGS_INVALID``, which would
    break removal for every account that has a hint.
    """
    if action.new_password is None:
        return ""
    if action.hint is not None:
        return action.hint
    return _text(pwd, "hint") or ""


async def dispatch_twofa_action(
    client: TelegramClient,
    action: SetTwoFactorPassword | ManageTwoFactorEmail,
) -> _DispatchResult:
    """The executor's single entry point for both 2FA write actions.

    One entry point for two actions on purpose: every match arm in
    ``_actions._dispatch_action`` costs cyclomatic complexity and that function sits
    exactly at ``tools/radon_gate.py``'s ceiling of 20. Branching here costs nothing.

    ``twofa_email_unconfirmed`` and ``code_length`` ride home on ``_DispatchResult``
    because there is no other way for the service to learn either: both exist only
    inside the ``EMAIL_UNCONFIRMED`` this module converts into a success.
    """
    # Local, and the only import in either direction that has to be: ``_twofa_email``
    # imports the refusal ladder, ``_srp`` and ``TwoFactorGatewayError`` from this
    # module at module scope, so naming it up top would close the loop.
    from core.telegram_client._twofa_email import (  # noqa: PLC0415
        dispatch_manage_twofa_email,
    )

    if isinstance(action, SetTwoFactorPassword):
        return await dispatch_set_twofa_password(client, action)
    return await dispatch_manage_twofa_email(client, action)


def twofa_log_extra(action: SetTwoFactorPassword | ManageTwoFactorEmail) -> dict[str, object]:
    """Log fields for both 2FA writes — booleans and the mode, never a secret.

    Every field these two actions carry is a password, a recovery email or a mailed
    confirmation code, so the log records what KIND of write happened and nothing
    about its payload. Lives here so the two share one ``_action_log_extra`` arm.
    """
    if isinstance(action, SetTwoFactorPassword):
        return {"has_hint": bool(action.hint), "removing": action.new_password is None}
    return {"mode": action.mode}
