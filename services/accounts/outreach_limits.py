"""Outreach limits — the operator-editable pacing of the contact lookup and bulk messages.

Thin orchestration over ``core.repositories.outreach``. Each run reads the limits
when it starts, so a save takes effect on the next run, never mid-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.repositories import outreach

if TYPE_CHECKING:
    from schemas.outreach_limits import OutreachLimits, OutreachLimitsUpdate


async def load_outreach_limits() -> OutreachLimits:
    """The saved limits, or the defaults while none were saved."""
    return await outreach.load_outreach_limits()


async def save_outreach_limits(data: OutreachLimitsUpdate) -> OutreachLimits:
    """Replace every limit at once."""
    return await outreach.save_outreach_limits(data)
