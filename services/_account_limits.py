"""Resolving one account's caps: its own override, else the fleet number.

Three caps bound an account, and until now all three were fleet-wide — one setting for
every account at once, so raising a cap for the one account that keeps hitting it raised
it for the whole fleet. The override row (``core.repositories.neurocomment._account_limits``)
adds a per-account layer; this module is the only place that decides which of the two wins,
so no gate can come to answer that question differently from another.

**The join cap has two fleet defaults, and one override.** Neurocomment and neuroshilling
carry separate ``max_joins_per_account_per_day`` settings but spend the SAME join log —
deliberately, because Telegram freezes an account on the total and does not care which
feature spent it. So the fleet default is passed in by the caller (each feature's own),
while an override, when present, is the account's budget for both. That is the only
reading of "this account may join N times a day" that is not self-contradictory.

**Absence is not zero.** ``None`` means "follow the fleet"; ``0`` means "no cap" on the
join and per-channel caps. An account with no row behaves exactly as it did before this
shipped, which is what makes the feature safe to deploy against a live fleet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.config import settings
from core.db import load_account_limit_override
from schemas.neurocomment_limits import EffectiveAccountLimits

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentSettings
    from schemas.neurocomment_limits import AccountLimitOverride


def _pick(override: int | None, fleet: int) -> int:
    """The account's own number, or the fleet's — ``None`` (never ``0``) means the fleet's."""
    return fleet if override is None else override


def resolve_limits(
    override: AccountLimitOverride | None,
    fleet: NeurocommentSettings,
) -> EffectiveAccountLimits:
    """The caps to enforce for one account — each one its override, else the fleet's.

    The comment caps come from the operator-editable settings row the caller already
    holds; the join cap has no row of its own and comes from config, which is why it is
    read here rather than threaded through every caller that never had it.
    """
    return EffectiveAccountLimits(
        max_joins_per_day=_pick(
            override.max_joins_per_day if override else None,
            settings.neurocomment.max_joins_per_account_per_day,
        ),
        max_comments_per_hour=_pick(
            override.max_comments_per_hour if override else None,
            fleet.max_comments_per_hour,
        ),
        max_comments_per_channel_per_day=_pick(
            override.max_comments_per_channel_per_day if override else None,
            fleet.max_comments_per_channel_per_day,
        ),
    )


async def account_limits(account_id: str, fleet: NeurocommentSettings) -> EffectiveAccountLimits:
    """One account's effective caps — a single-row read, for the per-account gate paths."""
    return resolve_limits(await load_account_limit_override(account_id), fleet)


async def account_join_cap(account_id: str, fleet_cap: int) -> int:
    """The rolling-24h join budget for one account: its override, else ``fleet_cap``.

    ``fleet_cap`` is the CALLER's own default — neurocomment's and neuroshilling's differ,
    while the override, being about the shared join log, governs both.
    """
    override = await load_account_limit_override(account_id)
    return fleet_cap if override.max_joins_per_day is None else override.max_joins_per_day
