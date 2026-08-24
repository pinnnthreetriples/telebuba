"""The "Лимиты" modal's read and write — one account's caps, spend and reset moments.

The gates that ENFORCE these caps read them through ``services._account_limits``; this
module is the operator's view of the same numbers, and deliberately does not re-derive
them: it resolves the override through that one resolver so the modal can never show a
cap the engine is not applying.

What it adds on top is the half a gate never needs — how much of each window is spent and
when the window hands a slot back. A rolling window has no midnight, so that moment is a
counted row's stamp plus the window length, and the modal can say "19:07 tomorrow" instead
of the useless "in 24 hours".

WHICH row is the whole subtlety. An account sitting exactly on its cap waits for the
oldest one. An account whose cap was LOWERED under a fuller window — the likeliest thing
to happen right after somebody reads this screen — is over by ``used - limit``, and every
one of those has to age out first, so the row that matters is the last of them. Naming the
oldest one was off by hours: three joins and a cap dropped to one reported a time three
hours before the account could actually join. The cap is therefore passed INTO each window
read, which resolves it against a count taken in the same transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.config import settings
from core.db import (
    account_busiest_channel_window,
    account_comment_window,
    account_join_window,
    fetch_account,
    load_account_limit_override,
    save_account_limit_override,
)
from schemas.neurocomment_limits import (
    AccountLimitGauge,
    AccountLimitOverride,
    AccountLimitsUpdate,
    AccountLimitsView,
    LimitWindow,
)
from services._account_limits import resolve_limits
from services.neurocomment import settings_store

_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)


def _resets_at(window: LimitWindow, length: timedelta) -> str | None:
    """The moment that row leaves the window, or ``None`` if nothing is counted.

    Counted rows are those at or after ``now - length``, so at exactly this stamp the row
    is still inside and the slot opens the instant after. The gauge is read to the minute,
    which is why that instant is not chased with an epsilon.
    """
    if window.slot_at is None:
        return None
    return (datetime.fromisoformat(window.slot_at) + length).isoformat()


def _gauge(
    *,
    limit: int,
    fleet_default: int,
    override: int | None,
    window: LimitWindow,
    length: timedelta,
) -> AccountLimitGauge:
    return AccountLimitGauge(
        limit=limit,
        used=window.used,
        fleet_default=fleet_default,
        overridden=override is not None,
        resets_at=_resets_at(window, length),
    )


async def _view(account_id: str, override: AccountLimitOverride) -> AccountLimitsView:
    fleet = await settings_store.load_settings()
    caps = resolve_limits(override, fleet)
    now = datetime.now(UTC)
    hour_ago = (now - _HOUR).isoformat()
    day_ago = (now - _DAY).isoformat()
    joins = await account_join_window(account_id, day_ago, caps.max_joins_per_day)
    hourly = await account_comment_window(account_id, hour_ago, caps.max_comments_per_hour)
    per_channel = await account_busiest_channel_window(
        account_id,
        day_ago,
        caps.max_comments_per_channel_per_day,
    )
    return AccountLimitsView(
        account_id=account_id,
        joins=_gauge(
            limit=caps.max_joins_per_day,
            # Neurocomment's fleet number, because this modal is neurocomment's. The join
            # LOG is shared with neuroshilling, and so is an override, but the two features
            # keep separate fleet defaults — see ``services._account_limits``.
            fleet_default=settings.neurocomment.max_joins_per_account_per_day,
            override=override.max_joins_per_day,
            window=joins,
            length=_DAY,
        ),
        comments_per_hour=_gauge(
            limit=caps.max_comments_per_hour,
            fleet_default=fleet.max_comments_per_hour,
            override=override.max_comments_per_hour,
            window=hourly,
            length=_HOUR,
        ),
        comments_per_channel_per_day=_gauge(
            limit=caps.max_comments_per_channel_per_day,
            fleet_default=fleet.max_comments_per_channel_per_day,
            override=override.max_comments_per_channel_per_day,
            window=per_channel,
            length=_DAY,
        ),
        busiest_channel=per_channel.channel,
    )


async def load_account_limits(account_id: str) -> AccountLimitsView | None:
    """Every cap of one account with its current spend, or ``None`` if no such account."""
    if await fetch_account(account_id) is None:
        return None
    return await _view(account_id, await load_account_limit_override(account_id))


async def save_account_limits(
    account_id: str,
    data: AccountLimitsUpdate,
) -> AccountLimitsView | None:
    """Replace the account's overrides, or ``None`` if no such account.

    Refusing the write is what keeps the table clean, not politeness about status codes:
    the row has no foreign key — it cannot have one and still be writable before the
    account is warmed — so a row written under a typo'd id would never be collected by
    anything. The repository makes that check inside the write transaction, so a delete
    landing mid-flight cannot slip one past it.

    Returning the whole view rather than the saved row keeps one round trip: a save that
    raises a cap changes what "осталось" says, and the numbers it says it against were
    never the caller's to compute.
    """
    override = await save_account_limit_override(account_id, data)
    if override is None:
        return None
    return await _view(account_id, override)
