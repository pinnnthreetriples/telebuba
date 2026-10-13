"""When a persistent bulk-message run may send, and how it spreads its sends.

Shared by the saved outreach limits (the defaults) and each run request (its own
copy). Times are wall-clock ``HH:MM`` in the recipient's zone when it is known and
``client_timezone`` is on, and in ``operator_timezone`` otherwise.
"""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

_CLOCK = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00")
MAX_CONCURRENCY = 10
MAX_SERIES_BREAK_SECONDS = 7200


def clock_minutes(value: str) -> int:
    """Minutes since midnight of an ``HH:MM`` that already passed validation."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


class RunSchedule(BaseModel):
    # The daily sending window, start inclusive and end exclusive; "24:00" ends at midnight.
    work_start: str = "09:00"
    work_end: str = "20:00"
    # Weekdays the run may send on, Monday = 0.
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5], min_length=1)
    operator_timezone: str = "Europe/Moscow"
    # Use the recipient's own zone where the phone number pins exactly one.
    client_timezone: bool = True
    # After this many sends an account rests for a break; 0 turns breaks off.
    series_size: int = Field(default=10, ge=0, le=100)
    series_break_min_seconds: int = Field(default=900, ge=0, le=MAX_SERIES_BREAK_SECONDS)
    series_break_max_seconds: int = Field(default=1800, ge=0, le=MAX_SERIES_BREAK_SECONDS)
    # How many accounts may be sending at the same moment.
    concurrency: int = Field(default=2, ge=1, le=MAX_CONCURRENCY)

    @field_validator("work_start", "work_end")
    @classmethod
    def check_clock(cls, value: str) -> str:
        if not _CLOCK.fullmatch(value):
            msg = "time must be HH:MM"
            raise ValueError(msg)
        return value

    @field_validator("work_days")
    @classmethod
    def check_days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value):  # noqa: PLR2004 - Monday..Sunday
            msg = "work_days are 0 (Monday) to 6 (Sunday)"
            raise ValueError(msg)
        return sorted(set(value))

    @field_validator("operator_timezone")
    @classmethod
    def check_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = "unknown operator_timezone"
            raise ValueError(msg) from exc
        return value

    @model_validator(mode="after")
    def check_ranges(self) -> RunSchedule:
        if clock_minutes(self.work_end) <= clock_minutes(self.work_start):
            msg = "work_end must be after work_start"
            raise ValueError(msg)
        if self.series_break_max_seconds < self.series_break_min_seconds:
            msg = "series break max must be at least min"
            raise ValueError(msg)
        return self
