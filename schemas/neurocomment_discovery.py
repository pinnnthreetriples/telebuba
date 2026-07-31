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

# What one source did on a run. ``ran`` = it answered at least once, ``failed`` = every
# attempt errored, ``skipped`` = it was never asked (disabled, no key, no seed). Nothing
# else is producible: a source either answers, refuses or is not consulted.
DiscoverySourceState = Literal["ran", "failed", "skipped"]

# The filter values the UI offers (``LANGUAGES``/``COUNTRIES`` in DiscoveryForm.tsx),
# which are also the ones ``core.telemetr`` can bridge to Telemetr.io's dictionaries.
# Literals rather than a shared constant because ``schemas/`` may not import ``core``;
# adding a region means editing all three lists. Accepting anything else was worse than
# useless: the catalogue answered with an empty page and the operator was told nothing.
DiscoveryLanguage = Literal["ru", "en", "ar", "de", "fr", "es", "tr", "uk", "kk", "uz", "fa", "hi"]
DiscoveryCountry = Literal[
    "RU", "KZ", "UZ", "UA", "BY", "DE", "FR", "ES", "GB", "TR", "AE", "SA", "EG"
]

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
# Bounded per item like ``AdoptHandle``: the validator below measures the *stripped*
# form, so without this a single 10 MB keyword passed validation and rode into a
# Telegram RPC.
Keyword = Annotated[str, Field(min_length=1, max_length=KEYWORD_MAX_LENGTH)]


class DiscoverySearchRequest(BaseModel):
    """Operator-supplied search parameters.

    ``language``/``country`` only reach Telemetr.io — Telegram's native search has
    no such filters, so they are refused without ``use_telemetr``: accepting them
    answered 202 for a run in which the filters reached nothing at all.
    ``members_min``/``members_max`` are applied by Telemetr server-side and re-applied
    client-side to native hits once the subscriber count is known.

    ``keywords`` come out stripped and deduped case-insensitively. Only the SPA deduped
    before, so a direct caller posting one keyword ten times spent ten identical Telegram
    RPCs against the flood budget and ten identical requests against a 1000/month quota.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[Keyword] = Field(min_length=1, max_length=MAX_KEYWORDS)
    seed_channel: str | None = Field(
        default=None,
        min_length=1,
        max_length=CHANNEL_HANDLE_MAX_LENGTH,
    )
    language: DiscoveryLanguage | None = None
    country: DiscoveryCountry | None = None
    members_min: int | None = Field(default=None, ge=0)
    members_max: int | None = Field(default=None, ge=0)
    # Off by default: the external catalogue costs quota and needs an operator key.
    use_telemetr: bool = False
    # Drop Telegram's own search and keep only the source that honours the locale
    # filters. Measured on the real cap: with four or more keywords and a productive
    # catalogue this costs ZERO rows and takes the locale-verified share from ~50% to
    # 100%. Off by default because a thin catalogue is the opposite case — a niche
    # filter on one keyword is 3 rows this way against 23 with Telegram alongside.
    catalogue_only: bool = False

    @model_validator(mode="after")
    def _check_bounds(self) -> DiscoverySearchRequest:
        deduped: list[str] = []
        seen: set[str] = set()
        for keyword in self.keywords:
            stripped = keyword.strip()
            if not (KEYWORD_MIN_LENGTH <= len(stripped) <= KEYWORD_MAX_LENGTH):
                msg = (
                    f"each keyword must be {KEYWORD_MIN_LENGTH}-{KEYWORD_MAX_LENGTH} "
                    "characters (Telegram rejects shorter global searches)"
                )
                raise ValueError(msg)
            if stripped.casefold() in seen:
                continue
            seen.add(stripped.casefold())
            deduped.append(stripped)
        self.keywords = deduped
        if self.seed_channel is not None and not self.seed_channel.strip():
            # A blank seed is truthy, so it survived into a pace sleep and a peer
            # resolution and yielded nothing. The rest of the normalization is the
            # service's (``core.channel_tokens`` is off limits to ``schemas/``).
            msg = "seed_channel must not be blank"
            raise ValueError(msg)
        if (self.language is not None or self.country is not None) and not self.use_telemetr:
            msg = "language/country need use_telemetr: only the catalogue can apply them"
            raise ValueError(msg)
        if self.catalogue_only and not self.use_telemetr:
            # Otherwise the run has no source at all and would report an empty success.
            msg = "catalogue_only needs use_telemetr: it is the only source left"
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


class DiscoverySourceReport(BaseModel):
    """What one source contributed to the last run, as the board shows it.

    Without this nothing told the operator that a filter had not applied: the board
    carried one ``last_error`` and a source that was skipped carried none at all.
    """

    source: DiscoverySource
    state: DiscoverySourceState
    # Rows the source returned, before dedup, the member filter and the candidate cap.
    hits: int = Field(default=0, ge=0)
    # Rows of the stored set this source produced. A channel two sources both returned
    # counts for both — crediting only the dedup winner is what hid the starvation.
    kept: int = Field(default=0, ge=0)
    # Of those, the ones NO other source found. ``kept`` alone still concealed a variant
    # of the same starvation: a catalogue whose rows were mostly duplicates of native
    # hits reported "50 of 60" while every one of its five own discoveries was cut.
    exclusive: int = Field(default=0, ge=0)
    # Did the source have more than it gave us? A flag, not a count: only the catalogue
    # advertises a total, per keyword, and summing those double-counts every channel two
    # keywords share — so the honest signal is that the page was capped, not a figure.
    truncated: bool = False
    # Short locale-neutral code (``telemetr_auth_failed``, ``FloodWait(120s)``), plus the
    # gateway's own diagnostic text when it gave one: a revoked key, an expired
    # subscription and a dead network are not the same problem to fix.
    reason: str | None = None
    detail: str | None = None


class DiscoveryCandidateOrigin(BaseModel):
    """Per-row provenance and catalogue geo for the run in flight.

    Deliberately NOT persisted: the candidate table has no column for either, and a
    migration against the operator's live database needs their approval. So this rides
    the board payload while the run's in-memory state lives, and a candidate read after
    a restart falls back to the stored single ``source``.
    """

    sources: list[DiscoverySource] = Field(default_factory=list)
    country: str | None = None
    language: str | None = None


class DiscoveryRunReport(BaseModel):
    """Everything the search stage knows beyond the rows it stored."""

    sources: list[DiscoverySourceReport] = Field(default_factory=list)
    # Keyed by candidate handle exactly as stored.
    origins: dict[str, DiscoveryCandidateOrigin] = Field(default_factory=dict)


class DiscoverySearchStageResult(BaseModel):
    """What ``run_search`` hands back to the run coordinator."""

    found: int = Field(default=0, ge=0)
    error: str | None = None
    # Was the stored candidate set actually replaced? A run that answered with nothing
    # usable leaves the previous, already-qualified set alone.
    replaced: bool = False
    # Did a Telegram FloodWait land? Qualification must not read on this account until
    # the window closes — the search stage has already written the cooldown.
    flooded: bool = False
    report: DiscoveryRunReport = Field(default_factory=DiscoveryRunReport)


class DiscoveryCandidate(BaseModel):
    """One found channel as the operator sees it."""

    channel: str = Field(min_length=1)
    title: str = ""
    subscribers: int | None = None
    source: DiscoverySource
    # Every source that returned this channel, not just the one whose spelling won.
    sources: list[DiscoverySource] = Field(default_factory=list)
    # What the catalogue filed the channel under, so a filter can be verified rather
    # than trusted. Only Telemetr.io supplies these, and only for the run in flight.
    country: str | None = None
    language: str | None = None
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
    # One entry per source the last run considered. Empty for a campaign that never
    # searched, or whose run predates this process.
    sources: list[DiscoverySourceReport] = Field(default_factory=list)


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
