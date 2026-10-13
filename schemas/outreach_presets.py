"""Operator-saved pacing presets for the bulk-send "pace and limits" block.

A preset is the four values the block's sliders set for one run. The built-in
presets live in the frontend next to the slider zones; only the operator's own
ones are stored. The bounds are the sliders' ranges.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

MAX_PRESETS = 20


class OutreachPresetWrite(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    # Centre of the jittered pause between first messages; a run waits 0.5x to 1.5x.
    pause_seconds: int = Field(ge=10, le=600)
    first_messages_per_day: int = Field(ge=5, le=60)
    contact_imports_per_day: int = Field(ge=3, le=40)
    lookup_batch_size: int = Field(ge=1, le=20)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            msg = "preset name is empty"
            raise ValueError(msg)
        return name


class OutreachPreset(OutreachPresetWrite):
    id: str
    updated_at: str


class OutreachPresetList(BaseModel):
    items: list[OutreachPreset]
