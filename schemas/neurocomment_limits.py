"""Per-account caps — the override row, the effective values, and the operator's gauge.

Three caps bound one account: channel joins per rolling 24h (shared with neuroshilling,
which spends the same join log), quota-spending comments per rolling hour, and
quota-spending comments per (account, channel) per rolling 24h. Until now all three were
fleet-wide — one number in ``settings.neurocomment`` / the settings row for every account
at once. These models add the per-account layer above them.

``None`` in :class:`AccountLimitOverride` and :class:`AccountLimitsUpdate` means *no
override*, not zero: zero is a real value on two of the three caps ("no cap"), so absence
has to be spelled differently from it. An account with no row at all therefore behaves
exactly as it did before this shipped.

Data contract only, no behaviour (non-negotiable #2). The resolution of an override
against the fleet value lives in ``services._account_limits``, and the windows the gauge
reports are measured in ``core.repositories.neurocomment._account_limits``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AccountLimitOverride(BaseModel):
    """One stored row: the caps this account does NOT take from the fleet."""

    account_id: str = Field(min_length=1)
    max_joins_per_day: int | None = Field(default=None, ge=0)
    max_comments_per_hour: int | None = Field(default=None, ge=1)
    max_comments_per_channel_per_day: int | None = Field(default=None, ge=0)


class EffectiveAccountLimits(BaseModel):
    """What the gates actually enforce for one account — override resolved, never null."""

    max_joins_per_day: int = Field(ge=0)
    max_comments_per_hour: int = Field(ge=1)
    max_comments_per_channel_per_day: int = Field(ge=0)


class LimitWindow(BaseModel):
    """What one rolling window currently holds — the repository's read shape.

    ``oldest_at`` is the stamp of the earliest row still inside the window; adding the
    window's length to it gives the moment the account wins a slot back. ``channel`` is
    filled only by the per-pair read, which has to name the pair it measured.
    """

    used: int = Field(ge=0)
    oldest_at: str | None = None
    channel: str | None = None


class AccountLimitGauge(BaseModel):
    """One cap as the operator reads it: the number, what is spent, when a slot returns.

    ``resets_at`` is the moment the OLDEST counted row leaves the rolling window, i.e.
    when the account gets one slot back — not a midnight reset, which no cap here has.
    ``None`` when nothing is counted, because then there is nothing to wait for.
    """

    limit: int = Field(ge=0)
    used: int = Field(ge=0)
    fleet_default: int = Field(ge=0)
    overridden: bool = False
    resets_at: str | None = None


class AccountLimitsView(BaseModel):
    """Every cap of one account, for the "Лимиты" modal."""

    account_id: str = Field(min_length=1)
    joins: AccountLimitGauge
    comments_per_hour: AccountLimitGauge
    # Measured on ``busiest_channel`` — the per-channel cap is per PAIR, so a single
    # number for the account only means anything once it names the channel it is about.
    comments_per_channel_per_day: AccountLimitGauge
    busiest_channel: str | None = None


class AccountLimitsUpdate(BaseModel):
    """The operator's edit — a full replace of the override row.

    Every field is sent every time: ``None`` is how the modal's "Сбросить" says *drop
    this override and follow the fleet again*, so an omitted field cannot be allowed to
    mean "keep whatever was stored" — that would leave no way to clear one cap.
    """

    model_config = ConfigDict(extra="forbid")

    max_joins_per_day: int | None = Field(default=None, ge=0)
    max_comments_per_hour: int | None = Field(default=None, ge=1)
    max_comments_per_channel_per_day: int | None = Field(default=None, ge=0)
