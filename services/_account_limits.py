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

**A soft start beats everything above it, an override included.** For the first days
after a «Мягкий старт» hand-off (``services._soft_start``) each cap — the override if
the account has one, else the fleet's — is cut to the soft one ("no cap", ``0``,
included), and a per-day comment cap appears. An override BELOW the soft cap still
wins, because the lower of the two is enforced; one above it waits for the soft period
to end and then applies as written. The soft start exists to protect a freshly handed
account, and an override typed in for the account's later life must not undo that.

**Absence is not zero.** ``None`` means "follow the fleet"; ``0`` means "no cap" on the
join and per-channel caps. An account with no row behaves exactly as it did before this
shipped, which is what makes the feature safe to deploy against a live fleet.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from core.config import settings
from core.db import fetch_warming_state, load_account_limit_override
from schemas.neurocomment_limits import EffectiveAccountLimits
from services._soft_start import soft_caps, soft_day

if TYPE_CHECKING:
    from schemas.neurocomment import NeurocommentSettings
    from schemas.neurocomment_limits import AccountLimitOverride


def _pick(override: int | None, fleet: int) -> int:
    """The account's own number, or the fleet's — ``None`` (never ``0``) means the fleet's."""
    return fleet if override is None else override


def _lower(cap: int, soft: int | None) -> int:
    """A cap cut down to the soft one; ``0`` ("no cap") becomes the soft cap."""
    if soft is None:
        return cap
    return soft if cap == 0 else min(cap, soft)


def resolve_limits(
    override: AccountLimitOverride | None,
    fleet: NeurocommentSettings,
    soft: int | None = None,
) -> EffectiveAccountLimits:
    """The caps to enforce for one account: its override, else the fleet's, under the soft cap.

    The comment caps come from the operator-editable settings row the caller already
    holds; the join cap has no row of its own and comes from config, which is why it is
    read here rather than threaded through every caller that never had it. ``soft`` is
    the day of a soft start (``services._soft_start.soft_day``), ``None`` outside one.
    """
    caps = soft_caps(soft) if soft is not None else None
    return EffectiveAccountLimits(
        max_joins_per_day=_lower(
            _pick(
                override.max_joins_per_day if override else None,
                settings.neurocomment.max_joins_per_account_per_day,
            ),
            caps.joins_per_day if caps else None,
        ),
        max_comments_per_hour=_lower(
            _pick(
                override.max_comments_per_hour if override else None, fleet.max_comments_per_hour
            ),
            caps.comments_per_hour if caps else None,
        ),
        max_comments_per_channel_per_day=_lower(
            _pick(
                override.max_comments_per_channel_per_day if override else None,
                fleet.max_comments_per_channel_per_day,
            ),
            caps.comments_per_channel_per_day if caps else None,
        ),
        max_comments_per_day=caps.comments_per_day if caps else 0,
    )


async def account_soft_day(account_id: str) -> int | None:
    """The day of this account's soft start, read off its warming row."""
    return soft_day(await fetch_warming_state(account_id), datetime.now(UTC))


async def account_limits(account_id: str, fleet: NeurocommentSettings) -> EffectiveAccountLimits:
    """One account's effective caps — a single-row read, for the per-account gate paths."""
    override = await load_account_limit_override(account_id)
    return resolve_limits(override, fleet, await account_soft_day(account_id))


async def account_join_cap(account_id: str, fleet_cap: int) -> int:
    """The rolling-24h join budget for one account: its override, else ``fleet_cap``, soft-capped.

    ``fleet_cap`` is the CALLER's own default — neurocomment's and neuroshilling's differ,
    while the override, being about the shared join log, governs both.
    """
    override = await load_account_limit_override(account_id)
    day = await account_soft_day(account_id)
    return _lower(
        _pick(override.max_joins_per_day, fleet_cap),
        soft_caps(day).joins_per_day if day is not None else None,
    )
