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

Data contract only, no behaviour — the boundary ``tests/test_architecture.py`` enforces.
The resolution of an override against the fleet value lives in ``services._account_limits``,
and the windows the gauge reports are measured in
``core.repositories.neurocomment._account_limits``.

``_CAP_MAX`` bounds the WRITE model only, and that asymmetry is the point. It exists
because sqlite raises ``OverflowError`` on an integer past 64 bits, which reached the API
as a 500. Putting the same ceiling on :class:`AccountLimitOverride` — the model built from
every stored row — would be a different thing entirely: a row already in the table is data,
not a request, and refusing to parse it does not undo it. It would only move the failure
somewhere far worse, because the bulk override load builds one of these per candidate, so a
single row above the ceiling would raise inside the selection pass and take the whole
fleet's turn down with it, plus both join gates and the GET behind them. Bound what comes
IN; read back whatever is there.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Sanity ceiling shared by the stored row and the operator's edit — see the module
# docstring. Kept in one place so the two models and the table's CHECKs cannot drift.
_CAP_MAX = 10_000


class AccountLimitOverride(BaseModel):
    """One stored row: the caps this account does NOT take from the fleet."""

    account_id: str = Field(min_length=1)
    # Deliberately unbounded above — see the module docstring. The floors stay: they are
    # what the column has always been able to hold, ``ge=1`` included.
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

    ``slot_at`` is the stamp of the row whose ageing-out puts the account back UNDER the
    cap it was read against. Usually the oldest row still inside the window; for an account
    whose cap was lowered below what the window already holds, every excess row has to age
    out first and this is the last of them. The cap therefore goes INTO the read: deciding
    which row matters from a separately-read count would let a join landing between the two
    reads pick the wrong row.

    ``channel`` is filled only by the per-pair read, which has to name the pair it measured.
    """

    used: int = Field(ge=0)
    slot_at: str | None = None
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

    max_joins_per_day: int | None = Field(default=None, ge=0, le=_CAP_MAX)
    # ``ge=1`` and not ``ge=0``: the hourly gate is a bare ``>=``, so a zero here would
    # refuse every comment rather than lift the cap. The fleet setting is bounded the same
    # way, and the modal gives this row its own minimum for exactly this reason.
    max_comments_per_hour: int | None = Field(default=None, ge=1, le=_CAP_MAX)
    max_comments_per_channel_per_day: int | None = Field(default=None, ge=0, le=_CAP_MAX)
