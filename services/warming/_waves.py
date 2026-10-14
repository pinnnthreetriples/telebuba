"""Warming waves: numbering a fresh stint and journalling a wave that ended unreleased.

A wave is one stint of one account life. The first stint is wave 1; every fresh stint
after it — a start from idle after a Stop, «Вернуть в прогрев» on a warmed account, a
start after a confirmed spam block — is the next wave. A restart that carries the
stint (the account was still warming) stays in its wave. The number lives on the
warming row, which a deletion takes with it, so a re-imported account starts again at
wave 1, exactly where the survival counter starts its new life.

A wave ends with a graduation (journalled as ``warmed``/``promoted``) or, unreleased,
with a ``warming_stopped`` row naming why and how many days it worked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from services.lifecycle_journal import record_lifecycle_event

if TYPE_CHECKING:
    from schemas.warming import WarmingStateRecord

WaveStopReason = Literal["operator", "spam_blocked"]


def next_wave(existing: WarmingStateRecord | None) -> int:
    """The wave a FRESH stint opens: one past the row's last stint, 1 for a first one."""
    if existing is None or existing.started_at is None:
        return 1
    return existing.wave + 1


async def record_wave_end(record: WarmingStateRecord, reason: WaveStopReason) -> None:
    """Journal that ``record``'s wave stopped without a graduation (never raises).

    Called while the stint is still the row's own — under the account lock for a Stop,
    from the loop task itself for the spam-block stop (a restart cancels and awaits that
    task before it restamps the stint) — so the row's snapshot names this wave's stint.
    """
    await record_lifecycle_event(
        record.account_id,
        "warming_stopped",
        {"reason": reason, "wave": record.wave, "worked_days": record.warmed_days},
    )
