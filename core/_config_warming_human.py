"""Warming tunables for "the account behaves like a person" (Прогрев 2.0, stage 4).

A mixin of :class:`core._config_warming.WarmingSettings`, split out for the file-size
gate like ``_config_warming_safety``: the fields are inherited, so their ``WARMING__*``
env names read like any other warming setting.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

_VISIT_KINDS = frozenset({"normal", "glance", "binge"})


class WarmingHumanSettings(BaseModel):
    """Real account age, character and chat style, visit cadence and visit types."""

    # Real account age (4.8), from the operator's registration date. Older than
    # ``dm_early_min_days``: DMs open ``dm_early_hours`` sooner. Older than
    # ``fast_phase_min_days``: «warming» from day 2. Day one stays «intro» regardless.
    registered_dm_early_min_days: int = Field(default=180, ge=0)
    registered_dm_early_hours: float = Field(default=24.0, ge=0.0)
    registered_fast_phase_min_days: int = Field(default=365, ge=0)
    # Character (4.2): AI fills the occupation and interests once per account; style is
    # drawn by code from the account id. Off = the plain prompt and the raw model line.
    character_enabled: bool = True
    chat_style_enabled: bool = True
    character_age_range: tuple[int, int] = (19, 45)
    # Anti-repeat across the fleet: interests overlap (Jaccard) below this, and no two
    # accounts with the same occupation; a profile that fails is asked again.
    character_interests_overlap_max: float = Field(default=0.5, ge=0.0, le=1.0)
    character_max_attempts: int = Field(default=3, ge=1)
    # Visit cadence (4.3): the smallest worthwhile visit, in actions (set_online
    # included), drawn per schedule. The phase cap divided by it bounds the persona's
    # visits a day: a cap of 15 affords 5-6 short visits.
    visit_actions_range: tuple[float, float] = (2.5, 3.0)
    # Visit types (4.4): weights per persona of a normal visit, a glance and a binge.
    # «Только ответить» joins the draw with its own weight while a partner's DM waits.
    persona_visit_weights: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "calm": {"normal": 0.5, "glance": 0.4, "binge": 0.1},
            "normal": {"normal": 0.5, "glance": 0.3, "binge": 0.2},
            "active": {"normal": 0.45, "glance": 0.25, "binge": 0.3},
        },
    )
    visit_reply_only_weight: float = Field(default=0.4, ge=0.0)
    # A glance books this many actions (set_online included) and reads one channel.
    visit_glance_actions: tuple[int, int] = (2, 3)
    # A binge books this multiple of the visit budget, walks more channels and lingers.
    visit_binge_budget_factor: float = Field(default=2.0, ge=1.0)
    visit_binge_channels: tuple[int, int] = (2, 4)
    visit_binge_linger_seconds: tuple[float, float] = (240.0, 720.0)
    # No binge before this phase: the first days stay short.
    visit_binge_min_phase: Literal["intro", "settling", "warming", "active", "warmed"] = "warming"
    # Step order: the story glance and one free read extra may come before the
    # channels; anything that writes never does.
    visit_stories_first_probability: float = Field(default=0.3, ge=0.0, le=1.0)
    visit_read_extra_first_probability: float = Field(default=0.3, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_ranges(self) -> WarmingHumanSettings:
        # ``randint``/``uniform`` on a reversed pair is a mid-cycle crash; fail at boot.
        pairs = (
            self.character_age_range,
            self.visit_actions_range,
            self.visit_glance_actions,
            self.visit_binge_channels,
            self.visit_binge_linger_seconds,
        )
        if any(lo > hi or lo < 0 for lo, hi in pairs) or self.visit_actions_range[0] <= 0:
            msg = "stage-4 warming ranges must satisfy 0 <= lo <= hi (visit actions > 0)"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_visit_weights(self) -> WarmingHumanSettings:
        for weights in self.persona_visit_weights.values():
            if not weights.keys() <= _VISIT_KINDS or any(w < 0 for w in weights.values()):
                msg = f"persona_visit_weights kinds must be {sorted(_VISIT_KINDS)}, weights >= 0"
                raise ValueError(msg)
            if sum(weights.values()) <= 0:
                msg = "persona_visit_weights need a positive weight per persona"
                raise ValueError(msg)
        return self
