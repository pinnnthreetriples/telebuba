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

- the SRP work ran on the event-loop thread, and for a server-supplied ``(p, g)``
  outside Telethon's fast path it can fail to terminate at all — the extracted
  sibling ``_twofa_srp`` owns the admission check, the offload and the bound;
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
imports the refusal ladder from here and is imported back only inside
:func:`dispatch_twofa_action` — see the comment there.

ponytail: KNOWN RESIDUAL, inherited and deliberately not fixed. TDLib re-encrypts
the Telegram Passport secure secret under the new password on every change;
``edit_2fa`` never did and neither does this replacement, so an account whose
Passport data was set up elsewhere loses access to it after a password change.
Telethon exposes no helper for it (there is no ``new_secure_settings`` arm in
``edit_2fa``), the fix is a second RPC plus its own crypto, and no Telebuba
workflow touches Passport — LOW for this product, but a real behaviour gap.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from telethon import errors
from telethon.password import compute_check, compute_digest
from telethon.tl.functions.account import GetPasswordRequest, UpdatePasswordSettingsRequest
from telethon.tl.types import InputCheckPasswordEmpty
from telethon.tl.types.account import Password, PasswordInputSettings

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._twofa_srp import (
    TwoFactorGatewayError,
    _ModPowAlgo,
    _srp,
    require_fast_algo,
)
from schemas.telegram_actions_twofa import (
    ManageTwoFactorEmail,
    SetTwoFactorPassword,
    TwoFactorStatusResult,
)

if TYPE_CHECKING:
    from telethon import TelegramClient

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
    # ``NEW_SETTINGS_EMPTY`` is documented for ``account.updatePasswordSettings`` as
    # "no password is set on the current account, and no new password was specified" —
    # the same fact one call later, reachable when the password disappears between the
    # ONE read and the write, so it collapses onto the same code.
    (errors.NewSettingsEmptyError, "twofa_password_not_set"),
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
    is why both halves of it go through ``_twofa_srp._srp`` instead of being awaited
    here, and why every algorithm is admitted by ``require_fast_algo`` BEFORE any of
    it is offloaded.
    """
    pwd = await _password_state(client)
    current_password = action.current_password
    if not _flag(pwd, "has_password"):
        if action.new_password is None:
            # With no password on the account there is nothing for a current one to
            # authorise, so a REMOVAL removes nothing. ``edit_2fa`` answered that with
            # ``False`` from a call it never sent for exactly this pair of ``None``s
            # (a removal against an account that HAS a password does send the
            # request): a no-op, never a success. The service's stale branch is what
            # actually resolves this state.
            code = "twofa_not_changed"
            raise TwoFactorGatewayError(code)
        current_password = None
    if current_password is not None:
        # The mirror of the recovery-email sibling's guard, and it is not decoration:
        # ``compute_check`` opens with ``request.current_algo``
        # (``telethon/password.py:137``), which is an optional TL flag — so an absent
        # one raises ``AttributeError``, a class outside this module's ladder that
        # reaches the operator as Telethon prose about a call carrying a password.
        _require_current_algo(pwd)
    algo = require_fast_algo(pwd.new_algo)
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
    hint = _resolved_hint(pwd, action)
    try:
        await client(
            UpdatePasswordSettingsRequest(
                password=proof,
                new_settings=PasswordInputSettings(
                    new_algo=algo,
                    new_password_hash=await _new_password_hash(algo, action),
                    hint=hint,
                ),
            ),
        )
    except errors.EmailUnconfirmedError as exc:
        return await _email_unconfirmed_result(client, action, exc, hint)
    except errors.RPCError as exc:
        raise _gateway_error(exc) from exc
    return _DispatchResult(twofa_hint=hint)


def _require_current_algo(pwd: object) -> None:
    """Refuse a write whose proof cannot be computed, before anything is computed.

    An absent ``current_algo`` is the "2FA is off" case reported one field over from
    ``has_password``, and it has its own code because the operator's answer differs:
    there is nothing to authorise against, not a challenge we cannot use.
    """
    current_algo = getattr(pwd, "current_algo", None)
    if current_algo is None:
        code = "twofa_password_not_set"
        raise TwoFactorGatewayError(code)
    require_fast_algo(current_algo)


async def _email_unconfirmed_result(
    client: TelegramClient,
    action: SetTwoFactorPassword,
    exc: errors.EmailUnconfirmedError,
    hint: str,
) -> _DispatchResult:
    """``EMAIL_UNCONFIRMED`` on a password write — decided from live state, per verb.

    A REMOVAL may never answer this as a success. ``execute`` would stamp it ``ok``,
    ``remove_account_twofa`` passes that straight to ``raise_for_result`` and never
    reads the unconfirmed flag — only ``set_account_twofa`` does — so it would go on
    to clear the column and tell the operator 2FA is off. If the write was not in
    force, the dashboard has just destroyed the only copy of a live cloud password.
    Its own stable code, so the answer is a refusal the service cannot mistake.

    For a set or a change the previous round claimed "TDLib holds its
    ``last_set_password_`` until the mailed code is typed back". **That member does
    not exist** — it is absent from ``PasswordManager.cpp`` and ``.h``, and the
    sentence was written here from an unverified claim. The real authority is
    TDLib's own API contract (``td_api.tl``, ``setPassword``): the change is held
    pending only when a NEW recovery email is specified in the same call, and this
    write specifies no email at all. So the ambiguity is settled the way TDLib
    settles it — treat the answer as success and RE-READ — rather than by assumption:
    one confirming ``account.getPassword``, and ``has_password`` decides. A read that
    fails, or one that says there is no password, stays conservative and reports
    applied-but-unconfirmed.

    ``code_length`` rides along either way, advisory, exactly as the recovery-email
    sibling threads it: it exists nowhere but inside this error.
    """
    if action.new_password is None:
        code = "twofa_removal_unconfirmed"
        raise TwoFactorGatewayError(code) from exc
    return _DispatchResult(
        twofa_email_unconfirmed=not await _password_is_live(client),
        twofa_email_code_length=exc.code_length or None,
        twofa_hint=hint,
    )


async def _password_is_live(client: TelegramClient) -> bool:
    """One confirming ``account.getPassword``: does Telegram hold a password now?

    Both verbs that reach it (a set and a change) end with a password present, so
    ``has_password`` is the whole check. Any failure answers ``False``: this is the
    conservative leg of an already-ambiguous outcome and must never upgrade an
    unknown into a confirmation.
    """
    try:
        return _flag(await client(GetPasswordRequest()), "has_password")
    except Exception:  # noqa: BLE001 - the write already landed; an unreadable state is a "no"
        return False


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
    # imports the refusal ladder and ``_password_state`` from this module at module
    # scope, so naming it up top would close the loop.
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
