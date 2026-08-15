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


class DiscoveryKeywordRequest(BaseModel):
    """One operator-typed topic, to be expanded into a keyword list by the LLM.

    Deliberately NOT campaign-scoped: expanding a topic reads no campaign state and
    spends no Telegram budget, so taking a campaign id would only invite the caller
    to believe it changes the answer.

    ``topic`` is bounded by ``KEYWORD_MAX_LENGTH`` rather than by a length of its
    own: it is the same kind of string the operator would otherwise have typed into
    ``DiscoverySearchRequest.keywords`` by hand, and it is interpolated into a
    prompt — an unbounded topic would be an unbounded prompt. Only ``min_length=1``
    below, not the keyword floor: a 2-character topic ("MMA") is a perfectly good
    thing to expand even though it is too short to search Telegram with, which is
    exactly why the operator is asking for an expansion.
    """

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=KEYWORD_MAX_LENGTH)


class DiscoveryKeywordResult(BaseModel):
    """The expanded keywords, or the short code saying why there are none.

    ``keywords`` come out ready to be posted straight back as
    ``DiscoverySearchRequest.keywords``: stripped, deduped case-insensitively,
    within ``KEYWORD_MIN_LENGTH``..``KEYWORD_MAX_LENGTH`` and capped at
    ``MAX_KEYWORDS``. The service applies those rules itself rather than trusting
    the model, so an answer the operator accepts wholesale cannot fail the search
    request's own validator.

    They are also single words, which is a tighter rule than this schema's — the
    search request happily takes a keyword containing spaces, and a hand-typed one
    still may. The suggestions cannot, because the SPA's keywords field splits what
    it receives on whitespace as well as commas, so a multi-word suggestion would
    reach the search as fragments of itself.

    ``error`` is a short locale-neutral code, exactly like
    ``DiscoverySourceReport.reason`` — the SPA maps it to text and renders the raw
    code when it has no copy. Three values, and each names a different thing for the
    operator to do:
      llm_unavailable — ``settings.deepseek.api_key`` is empty, so nothing was asked
                        at all; type the keywords by hand or set the key.
      llm_failed      — the gateway answered with an error, a rate limit, or no
                        text; retrying may work.
      llm_empty       — the model answered but nothing in it survived validation;
                        rephrasing the topic is what helps.
    ``str`` rather than a ``Literal`` for the same reason as ``reason``: the SPA
    already falls back to the raw code, so widening the set later must not be a
    breaking change to the generated client.

    A non-null ``error`` always carries an empty ``keywords`` list — there is no
    partial answer to report, since a model that produced anything usable is a
    success.
    """

    keywords: list[str] = Field(default_factory=list, max_length=MAX_KEYWORDS)
    error: str | None = None


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
    # DISTINCT usable channels the source returned, before the cross-source dedup, the
    # member filter and the candidate cap. Summing each attempt's own list instead
    # double-counted: three keywords returning the same three channels reported
    # "3 of 9", so a merge that lost nothing read as one that threw six rows away. Rows
    # with no public handle (invite links) are out of it too — no operator filter
    # controls them, so counting them made the same sentence overstate the loss.
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
    # Did this row enter the set without a subscriber count? The bounds can only be
    # applied to a hit Telegram returned a count for, and qualification backfills the
    # real number later — so without this flag the board shows "300" in a list the
    # operator filtered to "from 10 000" and nothing explains it.
    uncounted: bool = False


class DiscoveryRunReport(BaseModel):
    """Everything the search stage knows beyond the rows it stored."""

    sources: list[DiscoverySourceReport] = Field(default_factory=list)
    # Keyed by candidate handle exactly as stored.
    origins: dict[str, DiscoveryCandidateOrigin] = Field(default_factory=dict)
    # Did this run's findings actually reach the table? A rate limit leaves the previous
    # run's rows in place, and the strip must not then credit rows that exist nowhere.
    stored: bool = True
    # Did ``discovery_max_candidates`` cut the merged set? "Channels found: 100" is a
    # floor when it did, and reads as everything Telegram has when it did not say so.
    capped: bool = False


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
    probe already spends, so learning any of it costs no extra RPC — and every one of
    them keeps the tri-state contract
    :class:`schemas.telegram_action_results.LinkedDiscussionGroupResult` states.

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
    # Slow mode is on in the DISCUSSION GROUP — where the comments are actually written.
    # Its interval is deliberately absent: it is not in the probe's reply and reading it
    # would cost a second ``getFullChannel``, so the board says "slow mode" without a
    # number. The BROADCAST channel's own ``slowmode_seconds`` is not carried at all:
    # core.telegram.org documents it for supergroups only, so on the channel a campaign
    # adopts Telegram never sets it and the field was ``None`` in every real reply.
    group_slowmode_enabled: bool | None = None
    # Telegram's own marks on the broadcast channel — the entity the operator adopts.
    scam: bool | None = None
    fake: bool | None = None
    restricted: bool | None = None


class DiscoveryCandidate(BaseModel):
    """One found channel as the operator sees it."""

    channel: str = Field(min_length=1)
    title: str = ""
    subscribers: int | None = None
    # A plain string, like the stored row it comes from: see
    # :class:`DiscoveryCandidateRow`. The UI falls back to the raw code as its label.
    source: str = Field(min_length=1)
    # Every source that returned this channel, not just the one whose spelling won.
    # Plain strings for the same reason as ``source``: with no run state to read, this
    # falls back to the stored label.
    sources: list[str] = Field(default_factory=list)
    # The subscriber bounds never applied to this row — the search returned no count for
    # it, so it was admitted unfiltered and any number beside it arrived later, from the
    # qualification probe. ``False`` for a row whose provenance this process does not
    # hold (a board read after a restart), which says nothing either way.
    uncounted: bool = False
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
    # The candidates below are NOT the last run's: it stored nothing (a rate limit cut
    # it short), so they are the previous search's and must not be counted as this
    # run's find.
    stale_candidates: bool = False
    # ``total`` is a ceiling, not a total: the merge had more rows than
    # ``discovery_max_candidates`` and dropped the tail.
    capped: bool = False


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

    ``source`` is a plain string, NOT :data:`DiscoverySource`: this table is a live
    working set, and a row written by an older build names a source this one no longer
    has. Validated against the literal, such a row raised straight out of the
    repository and answered the whole board with a 500 — permanently, since the rows are
    only replaced by a run that stores something. The label is descriptive, so an
    unrecognised one is rendered verbatim rather than being made fatal.
    """

    channel: str = Field(min_length=1)
    title: str = ""
    subscribers: int | None = None
    source: str = Field(min_length=1)
    qualified_at: str | None = None
    qualify_error: str | None = None


class DiscoveryCandidateRows(BaseModel):
    rows: list[DiscoveryCandidateRow] = Field(default_factory=list)
