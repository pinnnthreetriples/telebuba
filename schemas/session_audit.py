"""Contracts for the session audit — every authorization Telegram lists for an account.

An account bought from a seller keeps working only while nobody else holds a session of
it: whoever does can terminate ours. The audit reads ``account.getAuthorizations`` and
says which of the listed sessions are the account's own (the ``.session`` files this
app signs in with) and which are not. No behaviour, no I/O.

``hash`` is a decimal STRING on the wire: Telegram's session hash is a signed 64-bit
integer, past what a JSON number carries without losing digits in the browser.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The refusals the audit raises by hand; the gateway's own (``session_dead``,
# ``account_deactivated``, ``account_frozen``, the flood family, ``unavailable``) are
# the shared action codes the SPA already translates.
SessionAuditRefusalCode = Literal[
    # The audit reads Telegram, and an account with no proxy would connect from the
    # operator's own IP, so it is refused before any call.
    "session_audit_proxy_missing",
    # Telegram answered with something the audit cannot read as a session list.
    "session_audit_failed",
]


class AccountSession(BaseModel):
    """One authorization of the account, as Telegram lists it."""

    model_config = ConfigDict(extra="forbid")

    # Telegram's ``Authorization.hash``, as a decimal string (see the module docstring).
    hash: str = Field(pattern=r"^-?[0-9]+$")
    # The session that asked: the primary device's own connection.
    is_current: bool
    # One of the account's OWN sessions — the primary's, or the other device's. Never
    # decided by what the session looks like, only by asking each of our clients which
    # authorization it is.
    is_ours: bool
    official_app: bool
    device_model: str
    platform: str
    system_version: str
    app_name: str
    ip: str
    country: str
    date_created: str | None = None
    date_active: str | None = None
    # Telegram flags a fresh login the user has not yet recognised.
    unconfirmed: bool = False


class AuthorizationList(BaseModel):
    """What the gateway reads: the sessions, and how long Telegram keeps an idle one."""

    sessions: list[AccountSession]
    # ``account.authorizations.authorization_ttl_days``: a session idle this long is ended.
    ttl_days: int | None = None


class AccountSessionAudit(BaseModel):
    """The account's sessions as of one audit, or an empty view when none ran yet."""

    account_id: str
    # ``None`` until the first audit.
    checked_at: str | None = None
    ttl_days: int | None = None
    # ``foreign_count`` is the sessions that are not ours: somebody else holds the
    # account too. Both counts are written by :meth:`of`, never typed by a caller.
    total: int = Field(ge=0)
    foreign_count: int = Field(ge=0)
    sessions: list[AccountSession]

    @classmethod
    def empty(cls, account_id: str) -> AccountSessionAudit:
        """The view of an account no audit has read yet."""
        return cls(account_id=account_id, total=0, foreign_count=0, sessions=[])

    @classmethod
    def of(
        cls,
        account_id: str,
        checked_at: str,
        listing: AuthorizationList,
    ) -> AccountSessionAudit:
        """The audit of ``listing`` read at ``checked_at``, counts derived from it."""
        return cls(
            account_id=account_id,
            checked_at=checked_at,
            ttl_days=listing.ttl_days,
            total=len(listing.sessions),
            foreign_count=sum(1 for session in listing.sessions if not session.is_ours),
            sessions=listing.sessions,
        )


class SessionAuditEvent(BaseModel):
    """One row of the audit history, kept to bracket the time of a later death."""

    account_id: str
    checked_at: str
    total: int = Field(ge=0)
    foreign_count: int = Field(ge=0)
    killed_count: int = Field(default=0, ge=0)
