"""Operator-editable pacing limits for the contact lookup and bulk first messages.

The numbers are not official Telegram limits: CRM vendors (Salebot, Whatcrm) report
that an account can add about 15 new contacts a day when spread over the day, and
that importing faster shrinks the next day's allowance and ends in a silent import
ban. The defaults follow those reports; the operator tunes them in the DB-backed
settings, and each run's form still overrides the per-run pacing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from schemas.run_schedule import RunSchedule

# Both run forms and these settings share one ceiling for a pause between calls.
MAX_PAUSE_SECONDS = 3600
MAX_LOOKUP_BATCH = 20
MAX_CONTACT_IMPORTS_PER_DAY = 200
MAX_FIRST_MESSAGES_PER_DAY = 500


class OutreachLimitsUpdate(BaseModel):
    """The whole settings object; a save replaces every field."""

    # New numbers one account may submit to ``contacts.ImportContacts`` per UTC day.
    contact_imports_per_day: int = Field(default=15, ge=1, le=MAX_CONTACT_IMPORTS_PER_DAY)
    lookup_batch_size: int = Field(default=3, ge=1, le=MAX_LOOKUP_BATCH)
    lookup_min_delay_seconds: float = Field(default=180, ge=0, le=MAX_PAUSE_SECONDS)
    lookup_max_delay_seconds: float = Field(default=480, ge=0, le=MAX_PAUSE_SECONDS)
    # Consecutive "not on Telegram" answers that mark an account as silently limited.
    silent_limit_streak: int = Field(default=8, ge=2, le=100)
    # Bulk-tool sends one account may make per UTC day; ``None`` turns the cap off.
    first_messages_per_day: int | None = Field(default=25, ge=1, le=MAX_FIRST_MESSAGES_PER_DAY)
    bulk_min_delay_seconds: float = Field(default=60, ge=0, le=MAX_PAUSE_SECONDS)
    bulk_max_delay_seconds: float = Field(default=180, ge=0, le=MAX_PAUSE_SECONDS)
    # Default sending window and spreading of a bulk-message run.
    schedule: RunSchedule = Field(default_factory=RunSchedule)

    @model_validator(mode="after")
    def validate_pauses(self) -> OutreachLimitsUpdate:
        if (
            self.lookup_max_delay_seconds < self.lookup_min_delay_seconds
            or self.bulk_max_delay_seconds < self.bulk_min_delay_seconds
        ):
            msg = "max delay must be at least min delay"
            raise ValueError(msg)
        return self


class OutreachLimits(OutreachLimitsUpdate):
    # ``None`` until the operator saves: the defaults above are then in effect.
    updated_at: str | None = None


class OutreachDailyUsage(BaseModel):
    """One account's outreach totals for one UTC day; also the shape of an increment."""

    account_id: str
    day: str
    contact_imports: int = 0
    contact_matches: int = 0
    first_messages: int = 0


class OutreachUsageList(BaseModel):
    """Today's (UTC) totals for the requested accounts, zeros where nothing was spent."""

    day: str
    items: list[OutreachDailyUsage]
