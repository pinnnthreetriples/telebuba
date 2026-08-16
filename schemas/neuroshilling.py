"""Pydantic contracts for the neuroshilling domain.

Several Telegram accounts play assigned roles and act out a scripted dialogue in
target chats. This module carries the data contracts flowing between
``core.repositories.neuroshilling`` (persistence), ``services.neuroshilling``
(policy) and ``api.v1.neuroshilling`` (transport). No behaviour, no I/O.

``NeuroshillingRefusalCode`` is the locale-neutral vocabulary of every refusal
this domain can answer with. It is declared whole rather than grown per stage so
that ``tests/test_error_code_i18n_parity.py`` can hold the SPA's ``shell.code.*``
table to it exhaustively — a code that appears later with no copy would
otherwise reach the operator as a raw snake_case token.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NeuroshillingMode = Literal["campaign", "revive"]
NeuroshillingScenarioStatus = Literal["draft", "approved"]
NeuroshillingRunMode = Literal["sequential", "parallel"]
NeuroshillingAutoresponder = Literal["off", "neurodialog"]
NeuroshillingReplyActivity = Literal["calm", "medium", "active"]
NeuroshillingStatus = Literal["idle", "running", "stopping", "done", "failed"]
NeuroshillingAccountState = Literal["active", "banned", "replaced"]
# Where ONE account stands with ONE target. Not a property of the target: a chat id
# is resolved out of the account's own session entity cache, and that cache is a
# separate file per account, so "we are in this chat" is only ever true of an
# account that itself joined. The five non-``pending`` states are the five outcomes
# Telegram answers a join with, and only two of them mean "play the dialogue here".
NeuroshillingPresenceState = Literal[
    "pending",
    "joined",
    # The chat gates entry and our request is queued: the account is NOT inside, so
    # running the steps here would fail every one of them.
    "pending_approval",
    # An expired or revoked invite, a chat that refuses us, or a peer shape whose
    # message ids are not shared between accounts.
    "refused",
    # Telegram rate-limited this account; it stops for the whole run.
    "flooded",
    # The account is out of the campaign entirely (e.g. at Telegram's 500-chat
    # ceiling), which is an ACCOUNT condition rather than a fault of this target.
    "retired",
]
# Who is holding an account right now, as the accounts modal reports it.
NeuroshillingBusyOwner = Literal["warming", "neuroshilling", "neurocomment"]
# One row of the send journal. ``pending`` is written BEFORE the dispatch and is the
# only state that consumes a quota slot without anything having been published yet —
# which is exactly why the quota predicate counts it. A row left ``pending`` after the
# dispatch returned means the request was already on the wire when the connection died
# and Telegram may have applied it, so it is never retried, and nothing the run does
# deletes it. Shortening the scenario does: ``_scenario._drop_steps_beyond`` removes the
# journal rows of steps that no longer exist, which a live run cannot reach because
# every scenario write is refused while the campaign is running.
NeuroshillingMessageStatus = Literal["pending", "sent", "failed", "skipped"]

NeuroshillingRefusalCode = Literal[
    "campaign_running",
    "too_many_targets",
    "no_targets",
    "not_enough_accounts",
    "account_busy",
    "role_without_account",
    "unknown_role",
    "run_mode_not_supported",
    "scenario_not_approved",
    "scenario_invalid",
    # The two halves of the outbound content filter, named apart because the operator's
    # edit differs: one line carries a link, the other carries a word the configured
    # list forbids. Folded into ``scenario_invalid`` they would send the operator
    # hunting through roles and delays for a fault that is in the text.
    "scenario_text_has_link",
    "scenario_text_forbidden_word",
    "llm_daily_limit_reached",
    "llm_unavailable",
    "generation_in_progress",
    "target_is_basic_group",
    "media_source_unreachable",
    # Distinct from the one above, because the operator's next move is opposite: the
    # media check never got an answer (a flood wait, a dead socket), so the link is
    # not the problem and editing it fixes nothing.
    "media_check_unavailable",
]

_MAX_NAME = 120
_MAX_TOPIC = 2000
# Twenty targets at Telegram's own username ceiling, with room for whole pasted
# links and the separators between them.
_MAX_TARGETS_RAW = 8000
_MAX_MEDIA_LINK = 500
_MAX_SECONDS = 3600
_MAX_LISTEN_MINUTES = 1440
# Posting-rate ceilings. Stage four reads these as the per-account rate, so an
# unbounded value is a ban rather than a big number: neurocomment's tuned figures
# for the same two rules are 10 an hour and 3 per chat a day
# (``core._config_domains``), and these sit a few times above them — room for an
# operator who wants to push, not room for a fleet posting once a second.
_MAX_PER_HOUR = 60
_MAX_PER_CHAT_PER_DAY = 50
# Lifetime ceiling per account for ONE campaign: 100 hours at the hourly cap.
_MAX_TOTAL_PER_ACCOUNT = 1000
# Mirrors ``schemas.neuroshilling_scenario.MAX_STEPS`` by value rather than by
# import: the scenario module imports this one and the dependency may not run
# back the other way. A position past the dialogue is refused at approval anyway;
# this only stops an unbounded integer reaching the column.
_MAX_STEP_POSITION = 50


class NeuroshillingAccountAssignment(BaseModel):
    """One account of a campaign's roster, as the operator arranged it."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    # Roles arrive with the scenario, so stage-one rosters are unassigned.
    role_id: str | None = None
    is_reserve: bool = False


class NeuroshillingCampaignAccount(BaseModel):
    """One row of ``neuroshilling_accounts`` as the repository reads it back.

    Separate from :class:`NeuroshillingAccountAssignment` because ``state`` is
    engine-owned (a ban writes it) and must never be settable from a request body.
    """

    account_id: str = Field(min_length=1)
    role_id: str | None = None
    is_reserve: bool = False
    state: NeuroshillingAccountState = "active"


class NeuroshillingPresence(BaseModel):
    """One row of ``neuroshilling_presence`` — an (account, target) membership record.

    ``chat_id`` is deliberately absent: it is per-account state that the run keeps in
    memory and re-resolves after a restart for one RPC, whereas a stored id would go
    stale the moment the account is replaced. What IS worth persisting is the
    invite-bearing ``target`` and the outcome, so a cold account can join later
    without re-deriving either.
    """

    account_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    state: NeuroshillingPresenceState = "pending"
    # Exception CLASS NAME, never its text — this travels over HTTP like every
    # other error field in the domain.
    last_error_type: str | None = None
    joined_at: str | None = None
    updated_at: str = Field(min_length=1)


class NeuroshillingCampaignCreate(BaseModel):
    """Opening a campaign asks for a name and nothing else — the rest has defaults."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    mode: NeuroshillingMode = "campaign"


class NeuroshillingCampaignUpdate(BaseModel):
    """Whole-form replacement of everything the operator edits on the page.

    Targets and the account roster travel here rather than through endpoints of
    their own: both are edited as part of one card and saving them separately
    would leave windows where the roster references a role the same save removed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=_MAX_NAME)
    mode: NeuroshillingMode = "campaign"
    topic: str = Field(default="", max_length=_MAX_TOPIC)
    targets_raw: str = Field(default="", max_length=_MAX_TARGETS_RAW)
    unique_messages: bool = True
    use_chat_context: bool = False
    media_message_link: str | None = Field(default=None, max_length=_MAX_MEDIA_LINK)
    media_step_position: int | None = Field(default=None, ge=1, le=_MAX_STEP_POSITION)
    run_mode: NeuroshillingRunMode = "sequential"
    pause_min_seconds: int = Field(default=10, ge=0, le=_MAX_SECONDS)
    pause_max_seconds: int = Field(default=20, ge=0, le=_MAX_SECONDS)
    messages_per_hour: int = Field(default=10, ge=1, le=_MAX_PER_HOUR)
    # 0 = no per-chat ceiling.
    messages_per_chat_per_day: int = Field(default=3, ge=0, le=_MAX_PER_CHAT_PER_DAY)
    # None = no lifetime ceiling for this campaign.
    total_per_account: int | None = Field(default=None, ge=1, le=_MAX_TOTAL_PER_ACCOUNT)
    reserve_enabled: bool = False
    autoresponder: NeuroshillingAutoresponder = "off"
    # Answering real people is OFF unless the operator turns it on: it is the one
    # switch that lets a stranger's text steer what gets published.
    reply_to_humans: bool = False
    reply_activity: NeuroshillingReplyActivity = "medium"
    listen_minutes: int = Field(default=60, ge=1, le=_MAX_LISTEN_MINUTES)
    accounts: list[NeuroshillingAccountAssignment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_pause_bounds(self) -> NeuroshillingCampaignUpdate:
        if self.pause_min_seconds > self.pause_max_seconds:
            msg = "pause_min_seconds must not exceed pause_max_seconds"
            raise ValueError(msg)
        return self


class NeuroshillingCampaign(BaseModel):
    """One row of ``neuroshilling_campaigns``."""

    campaign_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mode: NeuroshillingMode
    topic: str = ""
    targets_raw: str = ""
    unique_messages: bool = True
    use_chat_context: bool = False
    media_message_link: str | None = None
    media_step_position: int | None = None
    scenario_status: NeuroshillingScenarioStatus = "draft"
    run_mode: NeuroshillingRunMode = "sequential"
    pause_min_seconds: int = 10
    pause_max_seconds: int = 20
    messages_per_hour: int = 10
    messages_per_chat_per_day: int = 3
    total_per_account: int | None = None
    reserve_enabled: bool = False
    autoresponder: NeuroshillingAutoresponder = "off"
    reply_to_humans: bool = False
    reply_activity: NeuroshillingReplyActivity = "medium"
    listen_minutes: int = 60
    status: NeuroshillingStatus = "idle"
    run_id: str | None = None
    # Exception CLASS NAME, never its text: this field is served back over HTTP.
    last_error: str | None = None
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)


class NeuroshillingCampaignList(BaseModel):
    """Wrapper so callers never receive a bare list."""

    campaigns: list[NeuroshillingCampaign] = Field(default_factory=list)


class NeuroshillingBoardAccount(BaseModel):
    """One account of the pool, with whatever this campaign's roster says about it.

    ``assigned`` is what separates a rostered account from a merely offerable one;
    ``role_id``, ``is_reserve`` and ``state`` are meaningful only when it is true.
    ``busy_owner`` is what greys a row out in the picker, and
    ``busy_campaign_name`` is what lets the UI say WHICH campaign holds it rather
    than only that something does.
    """

    account_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    assigned: bool = False
    role_id: str | None = None
    is_reserve: bool = False
    state: NeuroshillingAccountState = "active"
    busy_owner: NeuroshillingBusyOwner | None = None
    busy_campaign_name: str | None = None


class NeuroshillingStepKey(BaseModel):
    """The journal's unique key: ONE step of ONE target in ONE run.

    A model rather than three loose arguments, because the triple IS the unique index
    and every lookup that drops a part of it goes silently wrong rather than failing.
    Aiming a reply by ``step_id`` alone is the example that matters: the link belongs
    to the campaign and the message id belongs to one send into one chat, so the
    dialogue in target two would answer target one's messages.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    step_id: str = Field(min_length=1)


class NeuroshillingQuotaUsage(BaseModel):
    """What one account has already spent, against the campaign's three ceilings.

    Read as one object because the three counts are always asked together, inside the
    account's quota lock, and they are only meaningful as a set.
    """

    # Rolling hour, whole history of the account — Telegram limits the account, and a
    # fresh run is not a fresh account.
    hour: int = Field(default=0, ge=0)
    # Rolling day, for THIS chat.
    chat_day: int = Field(default=0, ge=0)
    # Lifetime, for this campaign.
    campaign_total: int = Field(default=0, ge=0)


class NeuroshillingChatMessage(BaseModel):
    """One message the poller observed in a target chat.

    ``text`` is the only attacker-controlled string in the domain. It never reaches
    a log event's ``extra`` (which is an HTTP response body) and it reaches a model
    only through the fenced, per-message-trimmed block
    ``services.neuroshilling._prompt`` builds.

    ``is_ours`` is wider than the gateway's ``outgoing`` flag, which answers only
    "did the READING account write this". A sibling account's line is incoming to the
    reader, and it is recognised three ways: a scenario step by its journalled message
    id, a published autoreply by the row ``_autoreply`` writes here itself (it has no
    journal row to be found by), and a send whose id never came back by its sender.
    The distinction is what stops the fleet quoting itself back into its own context
    and answering its own lines.
    """

    message_id: int = Field(gt=0)
    # Recorded so a chat can be read back by author. Nothing acts on it today: the
    # reply decision is per MESSAGE, not per person.
    sender_id: int | None = None
    text: str = ""
    is_ours: bool = False


class NeuroshillingChatActivity(BaseModel):
    """What the listener has done in one campaign, for the launch card.

    ``seen`` counts observed messages including our own, because that is what the
    operator can check against the chat itself.
    """

    seen: int = Field(default=0, ge=0)
    replied: int = Field(default=0, ge=0)


class NeuroshillingRunStatus(BaseModel):
    """What the launch card shows: where the run is and how far it has got.

    ``sent`` counts delivered MESSAGE steps of the current run and ``total`` is
    targets x message steps. Reactions are journalled but counted in neither, because
    a skipped reaction is not lost progress.

    ``halted_accounts`` are the accounts Telegram has taken out of the run — a flood
    wait, a peer flood, or the 500-chat ceiling. They are read back from the durable
    presence rows rather than from a run-local set, so a restart does not forget them.

    ``substitutions`` is how many accounts a reserve one has taken over from. It is
    here rather than derived on the client because the board's roster carries
    ``state`` but not ``replaced_by_account_id``, and a ban with an empty reserve pool
    writes the first without the second.

    ``listening`` says the run is reading its target chats as well as writing to
    them. Not derivable on the client from the campaign row alone: the three switches
    that turn it on are stored, but whether a run is actually in flight is not.
    """

    status: NeuroshillingStatus = "idle"
    run_id: str | None = None
    sent: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    substitutions: int = Field(default=0, ge=0)
    listening: bool = False
    chat_messages_seen: int = Field(default=0, ge=0)
    human_replies_sent: int = Field(default=0, ge=0)
    # Exception CLASS NAME of whatever ended the last run, never its text.
    last_error_type: str | None = None
    halted_accounts: list[str] = Field(default_factory=list)


class NeuroshillingBoard(BaseModel):
    """One composite read backing the whole page.

    The account pool is ONE list, not a pool plus its rostered subset: the two
    carried the same objects and left the client joining them by id. It carries no
    derived counters either (role/step/account/target counts, dialogue length
    estimates): every one of those is an ``arr.length``, a ``reduce``, or a field of
    ``campaign`` already in this same payload, and a second copy could only drift.

    ``run`` is the exception and is here because it is NOT derivable: how many steps
    actually reached their chats and which accounts Telegram has halted are answers
    only the journal and the presence table hold.
    """

    campaign: NeuroshillingCampaign
    available: list[NeuroshillingBoardAccount] = Field(default_factory=list)
    # ``targets_raw`` parsed and normalised, in traversal order.
    targets: list[str] = Field(default_factory=list)
    run: NeuroshillingRunStatus = Field(default_factory=NeuroshillingRunStatus)
