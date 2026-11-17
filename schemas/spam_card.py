"""Contracts of the account card's spam block and of the fleet-level probe warning.

No behaviour, no I/O. ``services.spam_intel`` derives these from the stored Stage 2 projection
and the probe ledger (docs/spam-intel.md 5.10); the SPA renders them and decides nothing: every
field is a value the program derived (an enumerated source, an instant, a count), never a quoted
reply. ``tos_restricted`` is a flag BESIDE the verdict, never part of it.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - pydantic field types.
from typing import Literal

from pydantic import BaseModel, Field

from schemas.spam_appeals import AppealStatus  # noqa: TC001 - pydantic field type.
from schemas.spam_evidence import ConfidenceBucket, VerdictStatus  # noqa: TC001 - field types.
from schemas.spam_status import SpamStatusVerdict

# Where the verdict's deciding observation came from. ``none``: no deciding observation (an
# account nobody has looked at), or one the card does not name (a delivered message).
SpamSource = Literal["spambot", "peer_flood", "chat_ban", "freeze", "none"]


class SpamCheckRequest(BaseModel):
    """Body of ``POST /accounts/{id}/spam-check`` (every field optional: no body = defaults).

    ``confirm_out_of_turn``: the operator confirmed a check while the last probe is under
    ``out_of_turn_hours`` old. Without it such a click is refused with ``needs_confirmation``.
    """

    confirm_out_of_turn: bool = False


class SpamCheckResult(SpamStatusVerdict):
    """The Spam-check answer: the verdict, how it was served and when to ask again.

    ``probed``: a real question went to @SpamBot just now (``served_from == "probe"``).
    ``next_allowed_at``: the earliest instant another probe of the account may go out, an ISO
    instant, or ``None`` when it may now. For a ``deferred`` / ``refused`` answer see
    ``retry_at`` and ``reason``.
    """

    probed: bool = False
    next_allowed_at: str | None = None


class ProbeActivity(BaseModel):
    """What the ledger says about one account's real probes (``kind='probe'``)."""

    last_started_at: str | None = None
    count_7d: int = Field(default=0, ge=0)


class SpamCardSignals(BaseModel):
    """What one account's card needs beyond the legacy status, from stored data only."""

    verdict: VerdictStatus
    confidence: ConfidenceBucket
    source: SpamSource
    observed_at: datetime | None = None
    valid_until: datetime | None = None
    next_probe_at: datetime | None = None
    last_probe_at: datetime | None = None
    probes_7d: int = Field(default=0, ge=0)
    # The Spam-check click now would be out of turn and needs the operator's confirmation.
    out_of_turn: bool = False
    tos_restricted: bool = False
    tos_reason: str | None = None
    appeal_status: AppealStatus = "none"
    appeal_filed_at: datetime | None = None
    appeal_filed_by: str | None = None


class SpamBudgetStatus(BaseModel):
    """The fleet's probe use over the last 24 h against its caps, for the Accounts page warning.

    ``daily_cap_reached``: ``probes_24h`` is at or past the soft cap (routine probes now wait;
    at the hard cap nothing goes). ``out_of_turn_warned``: the operator's own checks in the
    window are at or past ``out_of_turn_warn_at`` (an out-of-turn check is an operator click
    on an account probed less than a day ago).
    """

    probes_24h: int = Field(ge=0)
    soft_cap: int = Field(ge=1)
    hard_cap: int = Field(ge=1)
    out_of_turn_24h: int = Field(ge=0)
    out_of_turn_warn_at: int = Field(ge=1)
    daily_cap_reached: bool
    out_of_turn_warned: bool
