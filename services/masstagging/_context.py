"""What a run reads once when it starts, and the «Темп» it reads live.

Settings cannot change under a run (a save is refused while one is attached), so one
read per run is the whole truth — except «Темп», which the board's gear saves at any
time and the run reads through :attr:`RunContext.pace`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.repositories import masstagging as repository
from schemas.masstagging import MasstaggingPace
from services.masstagging import _state
from services.masstagging.campaigns import settings_of

if TYPE_CHECKING:
    from schemas.masstagging import MasstaggingSettings


@dataclass(frozen=True)
class RunContext:
    campaign_id: str
    run_id: str
    settings: MasstaggingSettings
    # The last Start/Continue: "stop after N" counts from here.
    session_unix: int
    # Accounts that left this run (spam block, a dead session); they take nothing more.
    halted: set[str] = field(default_factory=set)

    @property
    def pace(self) -> MasstaggingPace:
        """«Темп» as it is now: the gear's latest save, else the run's own copy."""
        return _state.live_pace(self.campaign_id) or pace_of(self.settings)

    @property
    def stories(self) -> bool:
        return self.settings.mode == "stories"


def pace_of(value: MasstaggingSettings) -> MasstaggingPace:
    return MasstaggingPace.model_validate(
        value.model_dump(include=set(MasstaggingPace.model_fields))
    )


async def load_context(campaign_id: str, run_id: str) -> RunContext | None:
    # Before the read, not after: a gear save landing during it then survives as the
    # override instead of being dropped with a copy the read may have missed.
    _state.forget_pace(campaign_id)
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    return RunContext(
        campaign_id=campaign_id,
        run_id=run_id,
        settings=settings_of(record),
        session_unix=record.session_unix or 0,
    )
