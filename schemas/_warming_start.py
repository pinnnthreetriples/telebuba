"""Start-window models — split from ``schemas.warming`` for the file-size budget.

The operator picks how long an account rests before its first warming cycle
(``start_delay_hours``); the preview tells the start dialog when that lands in the
account's own clock, and whether the night rule moved it to the morning.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# The start dialog's "first cycle in" choice: one hour up to a full day.
StartDelayHours = Annotated[int, Field(ge=1, le=24)]

# The warming page's pool start previews every idle account in one request.
_MAX_PREVIEW_ACCOUNTS = 1000


class StartWindow(BaseModel):
    """The start request's first-cycle fields, inherited by ``StartWarmingRequest``."""

    # ``None`` leaves the loop's cold-start spread; see ``services.warming._start_options``.
    start_delay_hours: StartDelayHours | None = None
    # Pool start: each account lands at its own random moment inside the delay.
    spread_start: bool = False


class StartPreviewRequest(BaseModel):
    """Body of ``POST /warming/start-preview``."""

    account_ids: list[Annotated[str, StringConstraints(min_length=1)]] = Field(
        min_length=1, max_length=_MAX_PREVIEW_ACCOUNTS
    )
    start_delay_hours: StartDelayHours
    # Pool start: each account lands at its own random moment inside the delay.
    spread_start: bool = False


class StartPreviewItem(BaseModel):
    """When one account's first cycle can land, as UTC instants plus its zone."""

    account_id: str
    # IANA zone the night rule used (``UTC`` when the phone gives none).
    time_zone: str
    earliest_at: str
    latest_at: str
    # Part of the window falls in the account's night and moves to its morning.
    shifted: bool


class StartPreview(BaseModel):
    items: list[StartPreviewItem]
