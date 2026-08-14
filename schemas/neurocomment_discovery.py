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
#   telegram_search      — contacts.search per keyword (names and titles).
#   telegram_similar     — recommendations for the operator's own seed channel.
#   telegram_posts       — messages.searchGlobal per keyword, paged: channels whose
#                          POSTS match, so ones whose title never carries the keyword.
#   telegram_recommended — recommendations for the keyword sweep's own best hits, one
#                          read per seed. Separate from ``telegram_similar`` so a wave
#                          that ran cannot mask the seed pass's own reason.
DiscoverySource = Literal[
    "telegram_search",
    "telegram_similar",
    "telegram_posts",
    "telegram_recommended",
]

# What one source did on a run. ``ran`` = it answered at least once, ``failed`` = every
# attempt errored, ``skipped`` = it was never asked (no seed, or the run's read budget
# was already spent). Nothing else is producible: a source either answers, refuses or is
# not consulted.
DiscoverySourceState = Literal["ran", "failed", "skipped"]

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
    # Split on what the operator can do: ``account_busy`` = healthy account whose
    # session is taken (running listener, warming), so stop that and retry;
    # ``account_cooling`` = Telegram is rate-limiting it, so only waiting helps.
    "account_busy",
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

    ``members_min``/``members_max`` are applied client-side to the hits whose subscriber
    count Telegram happens to return.

    ``keywords`` come out stripped and deduped case-insensitively. Only the SPA deduped
    before, so a direct caller posting one keyword ten times spent ten identical Telegram
    RPCs against the flood budget.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[Keyword] = Field(min_length=1, max_length=MAX_KEYWORDS)
    seed_channel: str | None = Field(
        default=None,
        min_length=1,
        max_length=CHANNEL_HANDLE_MAX_LENGTH,
    )
    members_min: int | None = Field(default=None, ge=0)
    members_max: int | None = Field(default=None, ge=0)

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

    Without this nothing told the operator that a source had not answered: the board
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
    # of the same starvation: a source whose rows were mostly duplicates of the other's
    # reported "50 of 60" while every one of its five own discoveries was cut.
    exclusive: int = Field(default=0, ge=0)
    # Short locale-neutral code (``FloodWait(120s)``, ``seed_unusable``, ``read_budget``).
    reason: str | None = None
    # Did the run's shared read budget (``discovery_max_reads_per_run``) cut this source
    # short? Filled by every Telegram wave — ``telegram_search`` mid-sweep,
    # ``telegram_posts`` mid-paging, ``telegram_similar``/``telegram_recommended`` before
    # a read they never got to make — because a wave stopped by the budget returns fewer
    # channels than it could have, and the board must say so rather than let the operator
    # read a truncated run as an exhausted one.
    truncated: bool = False


class DiscoveryCandidateOrigin(BaseModel):
    """Per-row provenance for the run in flight.

    Deliberately NOT persisted: the candidate table has no column for it, and a
    migration against the operator's live database needs their approval. So this rides
    the board payload while the run's in-memory state lives, and a candidate read after
    a restart falls back to the stored single ``source``.
    """

    sources: list[DiscoverySource] = Field(default_factory=list)


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


class DiscoveryChannelVerdict(BaseModel):
    """Why a candidate is (or is not) a place this campaign can comment in.

    Every field rides the SAME ``channels.getFullChannel`` reply the comments-enabled
    probe already spends, so learning any of it costs no extra RPC.

    Tri-state on purpose: ``None`` means the reply did not answer that field (no linked
    group, an older TL layer, a field Telegram omitted) and NEVER "no". The board must
    render an unanswered signal as unknown, and no caller may block a channel on
    anything but an explicit positive verdict — never on falsiness.

    Deliberately NOT persisted, exactly like :class:`DiscoveryCandidateOrigin`: the
    candidate table has no column for it, and a migration against the operator's live
    database needs their approval. A candidate read after a restart therefore carries no
    verdict at all, which reads as "unknown", not as "fine".
    """

    # May anyone write in the discussion group at all? Positive sense: the wire carries
    # ``default_banned_rights.send_messages`` ("writing is banned for everyone").
    can_send_messages: bool | None = None
    # Commenting requires joining the discussion group first.
    join_to_send: bool | None = None
    # ...and that join needs an admin's approval — a dead end for an unattended campaign.
    join_request: bool | None = None
    # Slow mode is on in the DISCUSSION GROUP. Its interval is deliberately absent: it is
    # not in the probe's reply and reading it would cost a second ``getFullChannel``, so
    # the board says "slow mode" without a number rather than pairing this with the
    # broadcast's interval below, which describes a different chat.
    group_slowmode_enabled: bool | None = None
    # The BROADCAST channel's own slow-mode interval. Unbounded here on purpose: it is
    # copied verbatim from the gateway model, and a bound would turn odd Telegram data
    # into a ValidationError inside a background probe.
    broadcast_slowmode_seconds: int | None = None
    # Telegram's own marks on the broadcast channel — the entity the operator adopts.
    scam: bool | None = None
    fake: bool | None = None
    restricted: bool | None = None


class DiscoveryCandidate(BaseModel):
    """One found channel as the operator sees it."""

    channel: str = Field(min_length=1)
    title: str = ""
    subscribers: int | None = None
    source: DiscoverySource
    # Every source that returned this channel, not just the one whose spelling won.
    sources: list[DiscoverySource] = Field(default_factory=list)
    qualification: DiscoveryQualification
    # What the qualification probe learnt beyond comments on/off, while this process
    # holds the run's state. ``None`` = no verdict available (never probed here, or lost
    # to a restart), which is unknown — not fine.
    verdict: DiscoveryChannelVerdict | None = None
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
    # Locale-neutral short code (e.g. ``FloodWait(120s)``, ``seed_unusable``).
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


# Batch-only statuses: ``failed`` is a channel whose link attempt raised (the DB was
# locked, the campaign was deleted mid-batch), and ``comments_off`` is one the server
# refused because its cached qualification says the campaign could never comment there —
# the check the UI's disabled checkbox used to be the only enforcement of.
# ``ChannelLinkOutcome`` is deliberately left alone — the single-channel link route
# cannot produce either, and widening its literal would advertise statuses it never
# returns.
DiscoveryAdoptStatus = Literal["linked", "already_assigned", "comments_off", "failed"]


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
