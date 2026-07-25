"""Channel-discovery schemas — split from ``schemas.neurocomment`` (file-size cap).

Self-contained: nothing here imports ``schemas.neurocomment``, which keeps the split
one-way (that module must not import this one either), same arrangement as
``schemas.neurocomment_progress``.

Field bounds are literals, not config reads: ``schemas/`` may not import ``core``
(enforced by ``tests/test_architecture.py``). The runtime knobs that *do* live in
config (candidate cap, pacing, cache TTL) are applied in ``services/``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Where a candidate came from. Adding a source is one literal here plus one
# adapter in services (see .mex/patterns/add-discovery-source.md).
DiscoverySource = Literal["telegram_search", "telegram_similar", "telemetr"]

# Whether the channel accepts comments. ``pending`` = not probed yet, ``unknown`` =
# the probe failed (Telegram unreachable / channel gone), so the operator sees the
# difference between "wait" and "we could not tell".
DiscoveryQualification = Literal["pending", "comments_on", "comments_off", "unknown"]

# Run phase. The whole run is background — even the search stage spends ~20s on
# paced RPCs — so the UI needs to distinguish "still searching, no rows yet" from
# "searching done, checking comments" and from an empty result.
DiscoveryPhase = Literal["idle", "searching", "qualifying", "done", "failed"]

DiscoveryStartStatus = Literal[
    "started",
    "already_running",
    "no_account",
    "account_cooling",
    "daily_limit_reached",
]

# Telegram rejects global searches under 4 characters outright.
KEYWORD_MIN_LENGTH = 4
KEYWORD_MAX_LENGTH = 64
MAX_KEYWORDS = 10
# Upper bound on one adopt click. Onboarding's own rolling join cap (20/account/day)
# and 30-120s join jitter absorb the burst; this just bounds the request body.
# The ceiling of ``discovery_max_candidates``, so select-all can never silently drop a
# tail.
MAX_ADOPT_CHANNELS = 500
CHANNEL_HANDLE_MAX_LENGTH = 32

AdoptHandle = Annotated[str, Field(min_length=1, max_length=CHANNEL_HANDLE_MAX_LENGTH)]


class DiscoverySearchRequest(BaseModel):
    """Operator-supplied search parameters.

    ``language``/``country`` only reach Telemetr.io — Telegram's native search has
    no such filters. ``members_min``/``members_max`` are applied by Telemetr
    server-side and re-applied client-side to native hits once the subscriber count
    is known.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(min_length=1, max_length=MAX_KEYWORDS)
    seed_channel: str | None = Field(default=None, max_length=CHANNEL_HANDLE_MAX_LENGTH)
    language: str | None = Field(default=None, max_length=32)
    country: str | None = Field(default=None, max_length=32)
    members_min: int | None = Field(default=None, ge=0)
    members_max: int | None = Field(default=None, ge=0)
    # Off by default: the external catalogue costs quota and needs an operator key.
    use_telemetr: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> DiscoverySearchRequest:
        for keyword in self.keywords:
            stripped = keyword.strip()
            if not (KEYWORD_MIN_LENGTH <= len(stripped) <= KEYWORD_MAX_LENGTH):
                msg = (
                    f"each keyword must be {KEYWORD_MIN_LENGTH}-{KEYWORD_MAX_LENGTH} "
                    "characters (Telegram rejects shorter global searches)"
                )
                raise ValueError(msg)
        if (
            self.members_min is not None
            and self.members_max is not None
            and self.members_min > self.members_max
        ):
            msg = "members_min must not exceed members_max"
            raise ValueError(msg)
        return self


class DiscoverySearchOutcome(BaseModel):
    """Why a start attempt did or did not spawn a run."""

    status: DiscoveryStartStatus


class DiscoveryCandidate(BaseModel):
    """One found channel as the operator sees it."""

    channel: str = Field(min_length=1)
    title: str = ""
    subscribers: int | None = None
    source: DiscoverySource
    qualification: DiscoveryQualification
    # Already an active link on THIS campaign.
    in_campaign: bool = False
    # Active on another campaign — the one-active-campaign-per-channel guard means
    # adopting it would be refused, so the row is shown but not selectable.
    taken_by_other_campaign: bool = False


class DiscoveryProgress(BaseModel):
    phase: DiscoveryPhase
    running: bool = False
    total: int = Field(default=0, ge=0)
    qualified: int = Field(default=0, ge=0)
    comments_on: int = Field(default=0, ge=0)
    # Locale-neutral short code (e.g. ``FloodWait(120s)``, ``telemetr_rate_limited``).
    last_error: str | None = None


class DiscoveryBoard(BaseModel):
    campaign_id: str = Field(min_length=1)
    progress: DiscoveryProgress
    candidates: list[DiscoveryCandidate] = Field(default_factory=list)


class DiscoveryAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Per-item bounds too: adopt writes the handle it is given straight into
    # ``neurocomment_campaign_channels``, so without them a blank handle failed deep
    # inside the link transaction (a client mistake surfacing as a 500) and a 100 KB
    # string was persisted verbatim. The ceiling is Telegram's username length, which is
    # also what discovery itself can store: candidates are normalized against
    # ``CHANNEL_HANDLE_MAX_LENGTH`` and invite-style ``+HASH`` forms (the one legal form
    # that is longer) are dropped at search time, so adopt can never receive one.
    channels: list[AdoptHandle] = Field(min_length=1, max_length=MAX_ADOPT_CHANNELS)

    @model_validator(mode="after")
    def _check_handles(self) -> DiscoveryAdoptRequest:
        for channel in self.channels:
            # ``min_length`` lets " " and "\t" through, and a padded handle links a row
            # that matches no candidate and no linked group — the listener would simply
            # never watch it. Cheaper to refuse than to store something unusable.
            if channel != channel.strip():
                msg = "each channel must be a bare handle with no surrounding whitespace"
                raise ValueError(msg)
        return self


# Batch-only third status: ``failed`` is a channel whose link attempt raised (the DB was
# locked, the campaign was deleted mid-batch). ``ChannelLinkOutcome`` is deliberately
# left alone — the single-channel link route cannot produce this, and widening its
# literal would advertise a status it never returns.
DiscoveryAdoptStatus = Literal["linked", "already_assigned", "failed"]


class DiscoveryAdoptOutcome(BaseModel):
    """What happened to one channel of a batch adopt."""

    status: DiscoveryAdoptStatus
    channel: str = Field(min_length=1)


class DiscoveryAdoptResult(BaseModel):
    outcomes: list[DiscoveryAdoptOutcome] = Field(default_factory=list)


class DiscoveryCandidateRow(BaseModel):
    """Persisted candidate row as the repository stores and returns it.

    Separate from :class:`DiscoveryCandidate`: the wire model carries campaign
    membership flags that are computed per read, not stored.
    """

    channel: str = Field(min_length=1)
    title: str = ""
    subscribers: int | None = None
    source: DiscoverySource
    qualified_at: str | None = None
    qualify_error: str | None = None


class DiscoveryCandidateRows(BaseModel):
    rows: list[DiscoveryCandidateRow] = Field(default_factory=list)
