"""Contracts for closing sessions that are not ours (``account.resetAuthorization``).

Only the sessions the operator named are closed, one call per session, never Telegram's
"terminate all others": an account can hold a second sign-in of its own. A run answers
one outcome per requested hash, so a partial success is told as it happened. No behaviour,
no I/O.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Pydantic resolves this annotation at class-build time, so it cannot sit in a
# TYPE_CHECKING block.
from schemas.session_audit import AccountSessionAudit  # noqa: TC001

# What became of one requested session:
# closed      — Telegram ended it.
# gone        — it was no longer listed (or Telegram no longer knew its hash): done already.
# not_foreign — it is one of OUR sessions; refused, whatever was asked.
# too_new     — Telegram refused: our session is younger than 24 hours and cannot end others;
#               ``retry_after_seconds`` is the time left when it is known.
# flood_wait  — Telegram asked to wait ``retry_after_seconds``; the run stopped.
# failed      — Telegram or the connection refused; the run stopped (``error_type`` is the class).
# skipped     — not tried, because an earlier session ended the run.
SessionCloseStatus = Literal[
    "closed", "gone", "not_foreign", "too_new", "flood_wait", "failed", "skipped"
]

# Most sessions one request may name. Telegram keeps an account to a few dozen at most, and
# every one costs a paced call, so a longer list is a mistake, not a bulk job.
MAX_SESSIONS_PER_CLOSE = 50

# Telegram's signed 64-bit hash as a decimal string, as the audit lists it.
SessionHash = Annotated[str, Field(pattern=r"^-?[0-9]{1,20}$")]


class SessionCloseRequest(BaseModel):
    """The sessions the operator confirmed, by hash (decimal strings, as the audit lists them)."""

    hashes: list[SessionHash] = Field(min_length=1, max_length=MAX_SESSIONS_PER_CLOSE)


class SessionCloseOutcome(BaseModel):
    """One requested session's result."""

    hash: str
    status: SessionCloseStatus
    retry_after_seconds: int | None = None
    error_type: str | None = None


class SessionCloseResult(BaseModel):
    """A run's outcomes, and the account's sessions read again afterwards.

    ``audit`` is ``None`` only when the re-read itself failed: the closes already happened,
    so they are reported all the same.
    """

    account_id: str
    outcomes: list[SessionCloseOutcome]
    closed_count: int = Field(ge=0)
    audit: AccountSessionAudit | None = None
