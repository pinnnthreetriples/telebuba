"""Contracts for re-keying an account's session (its own auth key, unknown to the seller).

An imported seller session shares its auth key with whoever kept a copy, so the account is
signed in a second time as a fresh session of its own (login by token, approved by the
imported one), the session file is replaced and the old authorization, the key the seller
knows, is ended. Ending it may have to wait: Telegram refuses a session younger than 24
hours to end others, and whether a session made by a login token counts as young is not
documented, so a refusal schedules the close instead of failing the re-key. No behaviour,
no I/O.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Every refusal the re-key can end with, each with its own operator text: a code shared with
# another feature may have none here, and the operator would be shown the bare code.
RekeyRefusalCode = Literal[
    # The account has no proxy: a proxyless client would connect from the operator's own IP.
    "rekey_proxy_missing",
    # Another feature holds the account right now; its client must not be swapped under it.
    "rekey_account_busy",
    # An earlier re-key still has its old authorization to end.
    "rekey_pending",
    # ... and Telebuba gave up on it: the operator must close it in the session list.
    "rekey_needs_attention",
    # The imported session could not be read, so it cannot approve the new login.
    "rekey_approver_unavailable",
    # Telegram refused the login token (expired, already used, invalid).
    "rekey_token_rejected",
    # The account has a cloud password Telebuba does not have, or Telegram refused it.
    "rekey_twofa_missing",
    "rekey_twofa_invalid",
    # The new session signed in as a different Telegram user: it was undone.
    "rekey_user_mismatch",
    # A file with the scratch name is already in the sessions directory.
    "rekey_session_conflict",
    # The new session file could not be put in place, or did not work once it was; the
    # imported one was restored.
    "rekey_swap_failed",
    # Telegram never confirmed the login in time (the token was accepted, no answer came).
    "rekey_timeout",
    # The connection to Telegram (proxy, network, the pooled client) was not there.
    "rekey_unavailable",
    "rekey_flood_wait",
    # The imported session is dead (revoked, expired, account deactivated): nothing to replace.
    "rekey_session_dead",
    # The new login's listing did not name the imported authorization unambiguously: undone.
    "rekey_old_key_unknown",
    # The swap failed and the imported content could not be put back: neither authorization
    # was ended and the side copy is kept; the account needs a person.
    "rekey_incomplete",
    # "Close the old session" on an account whose old session is already closed, or that was
    # never re-keyed.
    "rekey_old_key_none",
    # Anything else Telegram answered; the new session was undone.
    "rekey_failed",
]


# Terminal states of a re-key whose old authorization Telebuba will not end on its own.
RekeyProblem = Literal["old_key_unknown", "old_key_is_ours", "rekey_incomplete"]

# What the card checks before the operator ends an old authorization by hand: the account
# is clean with SpamBot, the new session is old enough, a second sign-in of ours could
# receive a login code, and the cloud password and passkey are stored.
OldKeyCheckName = Literal[
    "spam_clean", "session_age", "fallback_session", "twofa_stored", "passkey_stored"
]


class OldKeyCheck(BaseModel):
    """One item of that checklist: met or not."""

    name: OldKeyCheckName
    ok: bool


class RekeyResult(BaseModel):
    """What the gateway did: the old authorization (to end) and the new one."""

    old_hash: str = Field(pattern=r"^-?[0-9]+$")
    new_hash: str = Field(pattern=r"^-?[0-9]+$")
    # The journal row of this re-key (``account_key_intents``): the service closes it once
    # the old hash is stored. ``None`` only for a caller that journals nothing.
    intent_id: int | None = None


class StoredRekey(BaseModel):
    """One account's last re-key: when, which authorization is to be ended and when."""

    account_id: str = Field(min_length=1)
    old_hash: str = Field(pattern=r"^-?[0-9]+$")
    rekeyed_at: str
    # Not before this: the next try to end the old authorization. ``None`` once it is ended.
    close_after: str | None = None
    closed_at: str | None = None
    # Why Telebuba stopped trying to end the old authorization; ``None`` while it still tries.
    problem: RekeyProblem | None = None


class AccountRekeyView(BaseModel):
    """The account's re-key state for the card: never re-keyed, pending, or done."""

    account_id: str
    rekeyed_at: str | None = None
    # The old authorization is ended (``closed_at``) or waits (``close_after``).
    closed_at: str | None = None
    close_after: str | None = None
    # Set when Telebuba stopped trying: the operator closes the old session by hand.
    problem: RekeyProblem | None = None
    # Whether Telebuba ends the old authorization on its own; off, the operator does it.
    auto_close: bool = False
    # Filled while the old authorization is alive and the operator may close it.
    checklist: list[OldKeyCheck] = Field(default_factory=list)
