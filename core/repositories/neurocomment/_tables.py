"""SQLAlchemy tables for the neurocomment domain (issue #114).

Kept in their own module so ``core.db`` stays within the file-size budget.
Importing this module registers the tables in ``core.db._metadata``; the
repository package pulls it in, and ``core.db`` imports the package before
``_get_engine`` runs ``create_all``. The partial unique index enforcing
"one active campaign per channel" is created in migration #11 and recreated over
the case fold in migration #39 — see ``_campaign_channel_matches`` below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    text,
)

from core.channel_tokens import channel_fold_sql
from core.db import _metadata

if TYPE_CHECKING:
    from sqlalchemy import TextClause

_neurocomment_campaigns = Table(
    "neurocomment_campaigns",
    _metadata,
    Column("campaign_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("prompt", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Per-campaign solver override (#14): NULL defers to the global flag (#148).
    Column("solver_enabled", Boolean, nullable=True),
)
_neurocomment_campaign_channels = Table(
    "neurocomment_campaign_channels",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("channel", String, nullable=False),
    Column("active", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    # "This channel will not let us write" (migration #42). Every K consecutive write
    # failures end a round: ``pause_rounds`` counts them and ``paused_until`` (ISO-8601
    # UTC, NULL = not paused) parks the channel meanwhile. Persisted rather than kept in
    # memory because the four rounds span four days and the process restarts far more
    # often than that. Relinking a channel inserts a FRESH row, so a re-linked channel
    # starts its rounds over — the operator asked for it again.
    Column("pause_rounds", Integer, nullable=False, server_default="0"),
    Column("paused_until", String, nullable=True),
)


def _channel_fold(column: Column[str]) -> str:
    """The migration #39 index expression over ``column``, as literal SQL text.

    Literal text and not SQLAlchemy's ``case()``: that renders the branch constants
    as bound parameters, and SQLite only matches a query against an expression index
    when the expression carries no variables.
    """
    return channel_fold_sql(f"{column.table.name}.{column.name}")


def _channel_matches(column: Column[str], channel: str) -> TextClause:
    """Match ``column`` against ``channel`` through the migration #39 fold.

    ``@News``, ``news`` and ``News`` are one Telegram channel; a ``+HASH`` invite key
    IS case-sensitive and compares verbatim. Both sides go through the SQL fold, so
    what the index refuses to insert is exactly what these reads find — see
    :func:`core.channel_tokens.channel_fold_sql` for why folding the probe in Python
    instead would make the two disagree.
    """
    # The handle is a BOUND parameter; only the fold expression (built from column
    # metadata) and the placeholder name are interpolated, so no caller input reaches
    # the SQL text. See the docstring above for why this cannot be an ORM construct.
    # Suppresses semgrep's blanket avoid-sqlalchemy-text audit rule.
    # nosemgrep
    return text(f"{_channel_fold(column)} = {channel_fold_sql(':probe')}").bindparams(
        probe=channel,
    )


def _campaign_channel_matches(channel: str) -> TextClause:
    """Match a campaign-channel link the way the one-active-campaign index folds handles."""
    return _channel_matches(_neurocomment_campaign_channels.c.channel, channel)


def _campaign_channels_match(channels: list[str]) -> TextClause:
    """The ``IN`` form of :func:`_campaign_channel_matches` (same fold, one query)."""
    names = [f"probe_{index}" for index in range(len(channels))]
    folded = ", ".join(channel_fold_sql(f":{name}") for name in names)
    # Same as above: every handle is bound, and the interpolated names are generated
    # from range(), never from a caller. Same avoid-sqlalchemy-text suppression.
    # nosemgrep
    return text(
        f"{_channel_fold(_neurocomment_campaign_channels.c.channel)} IN ({folded})"
    ).bindparams(
        **dict(zip(names, channels, strict=True)),
    )


_neurocomment_campaign_accounts = Table(
    "neurocomment_campaign_accounts",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("created_at", String, nullable=False),
    # Legacy single-channel pin (migration #25): superseded by the subset table
    # below (migration #29) and no longer read or written — kept only because
    # migrations are add-only. NULL = all campaign channels.
    Column("channel", String, nullable=True),
)
# Per-account channel SUBSET within a campaign (migration #29): one row per channel
# the account is pinned to. NO rows for a (campaign, account) pair = the account
# serves ALL of the campaign's channels (the default). Supersedes the scalar
# ``channel`` column above.
_neurocomment_campaign_account_channels = Table(
    "neurocomment_campaign_account_channels",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        primary_key=True,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("channel", String, primary_key=True),
    Column("created_at", String, nullable=False),
)
_neurocomment_linked_groups = Table(
    "neurocomment_linked_groups",
    _metadata,
    Column("channel", String, primary_key=True),
    Column("linked_chat_id", BigInteger, nullable=True),
    Column("comments_enabled", Integer, nullable=False),
    Column("checked_at", String, nullable=False),
)
_neurocomment_discovery_candidates = Table(
    # Per-campaign scratch set from the "Найти каналы" search (migration #38).
    # Replaced wholesale on each run; the comments-enabled verdict itself is NOT
    # duplicated here — ``neurocomment_linked_groups`` already is that cache, and a
    # second copy could disagree with it. ``qualified_at``/``qualify_error`` record
    # the *attempt*, which the cache cannot: a failed probe writes nothing there, so
    # without this the candidate would stay pending forever and progress never
    # reach 100%.
    "neurocomment_discovery_candidates",
    _metadata,
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        primary_key=True,
    ),
    # Handle exactly as Telegram returned it (canonical case) — adopt writes this
    # value verbatim, and campaign-channel matching folds case (migration #39), so a
    # handle the operator typed in lower case is still recognised as the same channel.
    # Cross-provider dedup folds case in memory before insert, so no key column.
    Column("channel", String, primary_key=True),
    Column("title", String, nullable=False, server_default=""),
    # NULL until known: contacts.Search does not reliably carry a subscriber count;
    # it is backfilled for free from getFullChannel during qualification.
    Column("subscribers", Integer, nullable=True),
    Column("source", String, nullable=False),
    Column("qualified_at", String, nullable=True),
    Column("qualify_error", String, nullable=True),
    Column("created_at", String, nullable=False),
)
_neurocomment_readiness = Table(
    "neurocomment_readiness",
    _metadata,
    Column("account_id", String, ForeignKey("accounts.account_id"), primary_key=True),
    Column("channel", String, primary_key=True),
    Column("joined", Integer, nullable=False),
    Column("captcha_passed", Integer, nullable=False),
    Column("ready", Integer, nullable=False),
    Column("checked_at", String, nullable=False),
    # Operator "Skip channel for this account" (#148); migration #15 backfills 0.
    Column("human_skipped", Integer, nullable=False, server_default="0"),
    # Auto-detected hard ban: the group's own participant record has this account
    # restricted from sending (UserBannedInChannelError alone is account-wide, not
    # per-group, so it never sets this — see services.neurocomment.bans).
    # Sticky (survives re-onboarding); migration #30 backfills 0. Cleared by a
    # successful "Проверить каналы" probe (can_send) or an operator retry.
    Column("banned", Integer, nullable=False, server_default="0"),
    # Approval-gated group ("join by request"): when the MOST RECENT request was sent
    # and how many have been sent. NULL / 0 = nothing outstanding; migration #41
    # backfills that. Never touched by upsert_readiness — a re-onboard must not reset
    # the counter, or the pair would re-request forever.
    Column("join_requested_at", String, nullable=True),
    Column("join_request_attempts", Integer, nullable=False, server_default="0"),
    # Automatic recovery from a lost discussion chat (#43): when the MOST RECENT re-join
    # went out and how many have. NULL / 0 = never retried. Never touched by
    # upsert_readiness, for the same reason as the join-request pair above — every failed
    # re-join re-writes the row, and a reset there would retry forever.
    Column("rejoin_attempted_at", String, nullable=True),
    Column("rejoin_attempts", Integer, nullable=False, server_default="0"),
    # The re-join budget has been spent AND reported: the pair left the chat, the log
    # carries its line and the board badges the account. Purely the "already said this"
    # mark — the rule itself still reads the counter above — because the review that
    # writes it runs every five minutes and would otherwise repeat both forever. Cleared
    # exactly where the counter is (``clear_rejoin_attempts``); migration #50 backfills 0.
    Column("rejoin_gave_up", Integer, nullable=False, server_default="0"),
    # WHY the pair is out of the chat (#44): the Telegram error class that parked it, or
    # NULL when nobody knows — a row from before this column, or a gateway failure that
    # carried no error type. Written BY upsert_readiness (unlike the two counter pairs
    # above): it describes the very write that parks the pair, so any other readiness
    # write means the loss it described is over and the column goes back to NULL.
    Column("access_lost_reason", String, nullable=True),
    # Refusals to write here that the per-group ban ladder could NOT confirm (#47), and
    # when the last one landed. Two inside the rule's window end the pair's stay in this
    # chat; a delivered comment clears both. Like the two counter pairs above, and unlike
    # ``access_lost_reason``, ``upsert_readiness`` never touches these: the pair stays a
    # member and keeps being re-onboarded, so a reset carried by that write would hand the
    # budget back on every pass. Migration #47 backfills 0 / NULL.
    Column("unconfirmed_bans", Integer, nullable=False, server_default="0"),
    Column("unconfirmed_ban_at", String, nullable=True),
    # The guardian bot would not let this pair speak (#49). ``captcha_retry_at`` is when
    # the sweep authorised the ONE re-solve the rule grants (NULL = not asked yet), and
    # ``captcha_gave_up`` is terminal: the pair stopped trying and left the chat, so
    # onboarding refuses it from then on. Like the two counter pairs above, and unlike
    # ``access_lost_reason``, ``upsert_readiness`` never writes EITHER — and for the same
    # reason: the give-up branch re-writes the readiness row on every failed pass, so a
    # reset carried by that write would hand the budget back on every tick and the pair
    # would re-solve forever, which is the exact loop this rule exists to end. Migration
    # #49 backfills NULL / 0.
    Column("captcha_retry_at", String, nullable=True),
    Column("captcha_gave_up", Integer, nullable=False, server_default="0"),
)
_neurocomment_comments = Table(
    "neurocomment_comments",
    _metadata,
    Column("channel", String, primary_key=True),
    Column("post_id", Integer, primary_key=True),
    Column(
        "campaign_id",
        String,
        ForeignKey("neurocomment_campaigns.campaign_id"),
        nullable=False,
    ),
    Column("account_id", String, ForeignKey("accounts.account_id"), nullable=False),
    Column("status", String, nullable=False),
    Column("comment_text", String, nullable=True),
    Column("comment_msg_id", Integer, nullable=True),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    # Set (migration #27) when a posted comment is later found deleted from the
    # channel — NULL = still live. Its status stays 'posted' (it *was* delivered).
    Column("deleted_at", String, nullable=True),
)
# Challenge audit-and-cache table (migration #14): one row per guardian-bot
# challenge encountered at onboarding. Doubles as the global solved-decision
# cache (a ``WHERE outcome='solved'`` projection). Indexes live in migration #14.
_neurocomment_challenges = Table(
    "neurocomment_challenges",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("challenge_hash", String, nullable=False),
    Column("account_id", String, nullable=False),
    Column("channel", String, nullable=False),
    Column("raw_text", String, nullable=False),
    Column("button_labels_json", String, nullable=False),
    Column("decision_json", String, nullable=True),
    Column("outcome", String, nullable=False),
    Column("decided_at", String, nullable=False),
    Column("outcome_at", String, nullable=True),
)
# Single-row table holding the active listener account id so the engine can
# re-point the listener at boot. ``id`` is pinned to 1 (migration #12).
# ``listener_running`` (migration #24) splits "which account is the listener"
# from "is the runtime actively subscribed": a paused runtime keeps its remembered
# ``listener_account_id`` while ``listener_running`` is 0, so reload/reboot no
# longer confuses pause with "снять слушателя" (remove).
_neurocomment_runtime = Table(
    "neurocomment_runtime",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("listener_account_id", String, nullable=True),
    Column("listener_running", Boolean, nullable=False, server_default="0"),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_neurocomment_runtime_single_row"),
)
# Single-row operator-editable neurocomment limits (migration #19). Empty until
# the operator saves; reads fall back to ``settings.neurocomment`` config.
_neurocomment_settings = Table(
    "neurocomment_settings",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("max_comments_per_hour", Integer, nullable=False),
    Column("max_comments_per_channel_per_day", Integer, nullable=False),
    Column("reply_delay_min_seconds", Float, nullable=False),
    Column("reply_delay_max_seconds", Float, nullable=False),
    Column("min_trust_score", Integer, nullable=False),
    Column("updated_at", String, nullable=False),
    CheckConstraint("id = 1", name="ck_neurocomment_settings_single_row"),
)
# Durable backing for the in-memory engine cooldowns (migration #34). One row per
# parked ``(account_id, channel)``: ``channel=''`` is the account-wide flood/peer-flood
# cooldown, a handle scopes a slow-mode cooldown to that chat. ``until`` is an ISO-8601
# UTC deadline. The in-memory map in ``services.neurocomment._state`` stays the hot read
# path; this table only lets a just-flooded account survive a process restart still parked.
_neurocomment_cooldowns = Table(
    "neurocomment_cooldowns",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("channel", String, primary_key=True),
    Column("until", String, nullable=False),
)
# Append-only per-account channel-join log (migration #35). One row per real
# JoinChannel/JoinDiscussionGroup RPC that returned ok, backing the rolling-24h
# per-account join cap: Telegram freezes an account after ~20-50 channel joins a
# day, so both join sites gate on this count. ``joined_at`` is an ISO-8601 UTC
# string; the (account_id, joined_at) index (migration #35) serves the window count.
_neurocomment_join_log = Table(
    "neurocomment_join_log",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("account_id", String, nullable=False),
    Column("joined_at", String, nullable=False),
    # The watch channel a listener join subscribed to (migration #40), so the join
    # cache survives a restart; NULL for a discussion-group join, which is keyed by
    # readiness instead and must never make the listener skip the channel itself.
    Column("watch_channel", String, nullable=True),
    # When Telegram proved this join no longer stands (kicked / banned / gone private),
    # migration #45. The row is never deleted: it is the only record that the join RPC was
    # spent, so the cap above must go on counting it, and the number of lost rows for a
    # pair IS the re-join attempt counter. NULL = the membership still stands.
    Column("lost_at", String, nullable=True),
)
