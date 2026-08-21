"""API-facing cloud-password (2FA) models — what the SPA sends and receives.

``AccountTwoFactorView`` follows the ``AccountPrivacyView`` error-envelope idiom:
a live read Telegram refused comes back as a populated ``error`` with no
``status``, so the card still renders instead of failing the request.

Secret handling is the whole point of the split between these three models. The
live state carries booleans and the PUBLIC hint only; ``has_stored_password``
answers "can this dashboard still authorise a change" without revealing what it
stored (the shape ``ProxyRead.has_password`` already uses). The plaintext exists
in exactly one of them, ``AccountTwoFactorCreated``, and only in the POST
response that just generated it. The recovery email and its mailed confirmation
code are handled the same way: they travel in request bodies and nowhere else —
no response echoes them, and no log extra carries them.

``TwoFactorRefusalCode`` is the domain's whole refusal vocabulary, in the shape
``NeuroshillingRefusalCode`` / ``WarmingRefusalCode`` already use: a ``Literal``
in ``schemas/`` so ``tests/test_error_code_i18n_parity`` can enumerate it. It
lives here, below both layers, because the codes are raised from BOTH — the
gateway builds most of them and the service raises
``twofa_password_not_stored`` — and a ``Literal`` in either layer would have to
be imported by the other.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Pydantic resolves these annotations at class-build time, so they cannot live
# in a TYPE_CHECKING block.
from schemas.telegram_actions_twofa import TwoFactorStatusResult  # noqa: TC001

# Telegram's own floor for a cloud password; a shorter one is refused by the
# server, so refusing it here saves a round trip and gives a field-level 422.
_MIN_PASSWORD_LENGTH = 8
# ``account.getPassword``'s hint is a short public label, not a sentence.
_MAX_HINT_LENGTH = 100
# The address check is deliberately this weak: ``email-validator`` is not a
# dependency of this project and a recovery email is not worth taking one for, so
# Telegram's own ``EMAIL_INVALID`` is the real validator and ``twofa_email_invalid``
# is how the operator hears about it. The bound is RFC 5321's maximum path length.
_EMAIL_SHAPE = r".+@.+"
_MAX_EMAIL_LENGTH = 254
# Telegram mails a short numeric code; the length it will accept comes back as
# ``EMAIL_UNCONFIRMED_<N>``, so this is only an upper sanity bound.
_MAX_EMAIL_CODE_LENGTH = 32

TwoFactorRefusalCode = Literal[
    # Raised by the gateway's Telethon-error ladder (``_twofa._TWOFA_ERROR_CODES``).
    "twofa_current_password_invalid",
    "twofa_settings_invalid",
    "twofa_email_code_invalid",
    "twofa_email_hash_expired",
    "twofa_email_invalid",
    # Raised by hand, so no ladder enumerates them and this ``Literal`` is the only
    # thing that makes them visible to the i18n parity guard.
    "twofa_not_changed",
    "twofa_password_not_set",
    "twofa_password_not_stored",
    # ``EMAIL_UNCONFIRMED`` answered a REMOVAL. On a set or a change that answer is
    # resolved by a live re-read, but a removal must never be reported as applied:
    # ``remove_account_twofa`` would clear the stored password on the strength of it
    # and leave a live cloud password with no copy anywhere. Its own code because the
    # operator's next step is different — re-read the state, do not retry blindly.
    "twofa_removal_unconfirmed",
    # An answered refusal proved Telegram did not apply a fresh set, and the UPDATE
    # that should have rolled the pre-write back ALSO failed. The column now holds a
    # password Telegram rejected, which blocks the not-stored guard for good, so the
    # operator has to hear that rather than a plain refusal.
    "twofa_rollback_failed",
    # The pre-flight ``account.getPassword`` never answered, so NOTHING was written —
    # the one refusal that exists to keep a dead read leg from being reported as a
    # write that may have landed.
    "twofa_state_unreadable",
    # The challenge Telegram sent cannot be used: an unimplemented KDF class, or a
    # ``(p, g)`` outside the pair ``telethon.password.check_prime_and_good``
    # short-circuits on — which ``_twofa_srp.require_fast_algo`` refuses BEFORE any
    # computation, because the alternative is a factorisation that never returns.
    # Telethon's own message for the same facts is bare ``ValueError`` prose about
    # its internals, so this is what the operator sees instead. FLEET-wide when it
    # fires: it means Telegram rotated its prime, not that this account is unusual.
    "twofa_password_algo_unsupported",
    # The SRP work outran its bound. Its own code rather than the one above because
    # the two mean opposite things operationally: with the admission check in place
    # "unsupported" is the expected answer to a rotated prime, whereas this one can
    # now only mean the machine is overloaded or Telethon's fast path changed shape
    # under us — and it leaks one of two private worker threads when it fires.
    "twofa_password_compute_timeout",
]


class AccountTwoFactorView(BaseModel):
    """One account's live 2FA state, or why it could not be read.

    ``has_stored_password`` is about THIS dashboard, not about Telegram: it says
    whether the password we set is still in the DB, which is what decides
    whether a change or a removal can be authorised at all. It is a boolean by
    design — see the module docstring.
    """

    status: TwoFactorStatusResult | None = None
    has_stored_password: bool = False
    error: str | None = None


class AccountTwoFactorUpdateRequest(BaseModel):
    """Set/change body. Both fields optional: no password means "generate one".

    ``extra="forbid"`` so a typo'd key 422s instead of silently generating a
    password the operator did not expect.
    """

    model_config = ConfigDict(extra="forbid")

    password: str | None = Field(default=None, min_length=_MIN_PASSWORD_LENGTH)
    hint: str | None = Field(default=None, max_length=_MAX_HINT_LENGTH)

    @model_validator(mode="after")
    def _hint_must_not_leak_the_password(self) -> AccountTwoFactorUpdateRequest:
        """Telegram shows the hint to ANYONE at the login prompt.

        A hint containing the password hands it to whoever holds the phone
        number, which is the attacker this feature exists to stop — so it is a
        trust-boundary refusal, not a style rule. The message names neither
        value: a validation error is echoed back in the 422 envelope.
        """
        if self.password is None or self.hint is None:
            return self
        if self.password.lower() in self.hint.lower():
            msg = "hint must not contain the password"
            raise ValueError(msg)
        return self


class AccountTwoFactorCreated(BaseModel):
    """The POST response — the ONLY model in this codebase carrying the plaintext.

    It appears in the response to the request that set the password and nowhere
    else: no read endpoint returns it, ``AccountRead`` does not carry it, and no
    log event or error message may contain it. Same contract as an API key —
    shown once at creation so the operator can copy it into a password manager,
    never shown again.

    ``stored`` is ``False`` when Telegram accepted the password but the DB write
    that remembers it failed. The password is returned regardless, because after
    a successful RPC this response is the operator's only copy; losing it would
    leave the account unrecoverable if its session is ever reset.

    ``confirmed`` is ``False`` in TWO cases, both meaning "Telegram may or may not
    hold this password": the request reached the wire and only the ANSWER was lost,
    or Telegram answered ``EMAIL_UNCONFIRMED`` and the one confirming
    ``account.getPassword`` that follows it did not come back saying a password is
    set. The password is returned either way: if Telegram DID apply it, this is the
    only copy anybody has, and discarding it would strand the account behind a
    password no human ever saw.

    ``previous_kept`` splits that unconfirmed case in three, and only a CHANGE can
    reach any of them. ``True``: the previously stored password was left in the
    database untouched and the live read says Telegram does have a password, so ONE
    of the two — it or ``password`` — is the live one and the operator has to check
    from the phone. ``None``: the previous one was kept, but the live read answered
    nothing, so "one of these two is in force" is not something this response can
    claim — Telegram may hold neither. ``False``: nothing was kept, either because
    this was a fresh set or because the live read said Telegram has no password at
    all, which makes the stored value stale by definition.
    """

    password: str
    hint: str | None = None
    stored: bool = True
    confirmed: bool = True
    previous_kept: bool | None = False


class AccountTwoFactorEmailRequest(BaseModel):
    """Attach-a-recovery-email body.

    The address is checked only for a shape that could not possibly be an email;
    see ``_EMAIL_SHAPE`` for why that is deliberate and what catches the rest.
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(pattern=_EMAIL_SHAPE, max_length=_MAX_EMAIL_LENGTH)


class AccountTwoFactorEmailConfirmRequest(BaseModel):
    """The code the operator read out of the letter Telegram just sent."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=_MAX_EMAIL_CODE_LENGTH)


class AccountTwoFactorEmailPending(BaseModel):
    """Outcome of attaching (or re-sending to) a recovery email.

    ``pending`` means Telegram has the address and has mailed a confirmation code
    that the operator still has to type back. ``pending=False`` is the rarer
    branch: Telegram accepted the address as already verified and asked for
    nothing, so the flow is finished in one step.

    ``code_length`` is the length Telegram will accept, and it exists only because
    ``EMAIL_UNCONFIRMED_<N>`` reports it; ``None`` means Telegram did not say (the
    resend path), not that any length goes. Neither field is sensitive — the
    address and the code itself never leave the request they arrived in.
    """

    pending: bool
    code_length: int | None = None
