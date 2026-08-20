"""Cloud-password (2FA) Telegram actions — ``account.getPassword`` / ``edit_2fa``.

Sibling of ``telegram_actions_privacy.py`` (file-size cap); the discriminated
unions in ``schemas.telegram_actions`` import these names back, so callers keep
importing every action from ``schemas.telegram_actions`` unchanged.

Why this cluster exists: an account with no cloud password can be taken over by
anyone who gets its phone number and one login code. Setting one is the only
per-account defence the dashboard can apply, and the live state (is there a
password, what hint does the login prompt show) is readable only from Telegram.

``SetTwoFactorPassword`` is ONE action for all three writes, because Telethon's
``edit_2fa`` is one call for all three: ``new_password`` alone sets, both fields
change, and ``current_password`` alone REMOVES. It carries a plaintext secret,
so nothing may put its fields in a log extra or an exception message — the
``core.telegram_client._twofa`` module docstring states the rule for the
dispatch side, and ``tests/core/telegram_client/test_twofa.py`` asserts it.

``ManageTwoFactorEmail`` is one action for all FIVE recovery-email operations for
a different reason: each write action costs ``_actions._dispatch_action`` a match
arm plus an ``_action_log_extra`` arm, and that module is at its file-size budget
with its cyclomatic complexity already at ``tools/radon_gate.py``'s ceiling. A
``mode`` discriminator inside one action costs neither.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GetTwoFactorStatus(BaseModel):
    """Read-only: does this account have a cloud password, and what does it show?"""

    action_type: Literal["get_twofa_status"] = "get_twofa_status"


class TwoFactorStatusResult(BaseModel):
    """Gateway output for ``GetTwoFactorStatus`` — booleans plus the public hint.

    Every field is what ``account.getPassword`` reports, and every one of them is
    optional in the TL schema, so an absent flag degrades to the "no password"
    default rather than being guessed at. ``pending_reset_date`` is an ISO string
    (repo convention at the JSON boundary), never the ``datetime`` Telethon
    returns. The password itself is not here and cannot be: Telegram never
    returns it, and neither does this codebase after the one POST response.
    """

    has_password: bool = False
    hint: str | None = None
    has_recovery: bool = False
    pending_reset_date: str | None = None
    # A recovery email that was attached but whose confirmation code has not been
    # typed back yet. It is a different fact from ``has_recovery`` (a CONFIRMED
    # recovery email) and from ``account.Password.login_email_pattern``, which
    # belongs to an unrelated feature (receiving login codes by email) and is
    # deliberately not carried here — nothing in the SPA renders it.
    email_unconfirmed_pattern: str | None = None


class SetTwoFactorPassword(BaseModel):
    """Set, change or remove the cloud password — the field pair decides which.

    ``new_password`` alone sets a password on an account that has none;
    both fields change an existing one; ``current_password`` alone removes it.
    Both ``None`` is refused here: that is the ONE combination Telethon answered
    with ``False`` from ``edit_2fa`` without issuing any RPC, so the action would
    report a success it never attempted. (A removal against an account that has a
    password does send the request — the no-op is the pair of ``None``s, not the
    verb.)

    ``hint`` is shown at the login prompt to anyone holding the phone number, so
    it is public text — the API layer is what refuses a hint containing the
    password (``schemas.twofa.AccountTwoFactorUpdateRequest``). Three-valued on
    purpose: ``None`` means KEEP whatever Telegram currently shows and ``""`` means
    CLEAR it. ``account.updatePasswordSettings`` always writes the field, so without
    the distinction a change that simply did not mention a hint would erase the one
    the operator set — the gateway resolves ``None`` against a fresh
    ``account.getPassword``.
    """

    action_type: Literal["set_twofa_password"] = "set_twofa_password"
    # ``repr=False`` on every secret field: Pydantic renders each field into
    # ``__repr__``, and a stack-frame local rendered by ``repr()`` is exactly what
    # an error tracker ships (see the ``sentry_sdk.init`` comment in
    # ``core/logging.py``). Same idiom as ``core.config.LoggingSettings.sentry_dsn``.
    # This is defence in depth for f-string / ``repr()`` sinks only — Telethon's own
    # frames hold the plaintext as bare strings, which only that switch covers.
    current_password: str | None = Field(default=None, repr=False)
    # ``min_length=1`` is the verb, not a policy: ``None`` REMOVES the password and a
    # present value SETS one, so ``""`` names neither — it would be hashed like a
    # real password rather than removing anything. The API layer's own
    # ``min_length=8`` (Telegram's floor) keeps it off the HTTP path, but this
    # boundary must hold for any caller, not only that one.
    new_password: str | None = Field(default=None, repr=False, min_length=1)
    hint: str | None = None

    @model_validator(mode="after")
    def _check_any_password(self) -> SetTwoFactorPassword:
        if self.current_password is None and self.new_password is None:
            msg = "at least one of current_password/new_password must be set"
            raise ValueError(msg)
        return self


class ManageTwoFactorEmail(BaseModel):
    """The recovery-email flow: attach, clear, confirm, resend, cancel.

    Not a callback flow. Telethon's ``edit_2fa`` would take an
    ``email_code_callback``, but an unattended backend cannot read a mailbox — so
    the operator reads their own letter and types the code into a second request.
    That is why ``confirm`` is a mode here rather than a continuation of ``set``.

    ``clear`` and ``cancel`` are NOT the same operation and cannot be merged:
    ``cancel`` abandons a PENDING verification (``account.cancelPasswordEmail``),
    while ``clear`` detaches an already CONFIRMED address — which only
    ``account.updatePasswordSettings`` with an empty ``email`` can do. It therefore
    rides the same password-field-omitting path as ``set`` and needs the same
    ``current_password`` to authorise itself.

    Field requirements are per mode and enforced below rather than by five
    separate models, for the file-budget reason in the module docstring.
    ``current_password`` and ``code`` are secrets and ``email`` is personal data:
    none of the three may reach a log extra or an error message.
    """

    action_type: Literal["manage_twofa_email"] = "manage_twofa_email"
    mode: Literal["set", "clear", "confirm", "resend", "cancel"]
    # ``repr=False`` for the reason ``SetTwoFactorPassword`` documents above.
    current_password: str | None = Field(default=None, repr=False)
    email: str | None = Field(default=None, repr=False)
    code: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def _check_mode_fields(self) -> ManageTwoFactorEmail:
        if self.mode == "set" and (self.current_password is None or self.email is None):
            msg = "mode='set' requires current_password and email"
            raise ValueError(msg)
        if self.mode == "clear" and self.current_password is None:
            msg = "mode='clear' requires current_password"
            raise ValueError(msg)
        if self.mode == "confirm" and self.code is None:
            msg = "mode='confirm' requires code"
            raise ValueError(msg)
        return self
