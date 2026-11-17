"""Contracts of the semi-automatic appeal of a spam block (docs/spam-intel.md section 5.12).

No behaviour, no I/O. A HUMAN does the appeal in the account's own Telegram Web window; the
program only keeps a record that the operator says it was filed (who, when, what the verdict
said then) and closes it when @SpamBot answers ``clean``. Nothing here can carry appeal text:
every field is a value the program derived or the signed-in operator's name.
"""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - pydantic field types.
from typing import Literal

from pydantic import BaseModel

from schemas.spam_status import SpamStatusKind  # noqa: TC001 - pydantic field type.

# What closes an appeal. One kind today: a persisted @SpamBot ``clean`` (owner decision D4:
# only a real answer says the block is gone).
AppealClosedBy = Literal["spambot_clean"]

# ``none``: no appeal was ever filed for the account; ``open``: filed and not answered by a
# ``clean`` yet; ``closed``: the newest appeal ended (the card shows when).
AppealStatus = Literal["none", "open", "closed"]


class SpamAppeal(BaseModel):
    """One stored appeal row (``spam_appeals``)."""

    id: int
    account_id: str
    filed_at: datetime
    # The signed-in operator's username, as the session identifies them.
    filed_by: str
    # The verdict and the day the limit ends (``None`` = open-ended) at the moment of filing.
    verdict: SpamStatusKind
    until_day: date | None = None
    closed_at: datetime | None = None
    closed_by_kind: AppealClosedBy | None = None


class SpamAppealState(BaseModel):
    """What the account card shows about the appeal; one value per account."""

    account_id: str
    status: AppealStatus = "none"
    filed_at: datetime | None = None
    filed_by: str | None = None
    filed_verdict: SpamStatusKind | None = None
    filed_until_day: date | None = None
    closed_at: datetime | None = None
    closed_by_kind: AppealClosedBy | None = None
    # When the open appeal is re-checked through the gateway (a decision ask under the
    # budget): the end day plus a jitter, or ``appeal_recheck_hours`` after filing for an
    # open-ended limit. ``None`` when nothing is open.
    recheck_at: datetime | None = None


class SpamAppealList(BaseModel):
    """The open appeals of every account (one query, for the accounts table)."""

    items: list[SpamAppealState]
