"""Overflow settings domains — split from ``core.config`` for the file-size budget.

Holds the larger self-contained nested namespaces (warming, gemini, trust,
neurocomment). They are re-exported from ``core.config`` so existing
``from core.config import WarmingSettings`` call sites keep working unchanged;
the ``Settings`` aggregate and the ``settings`` instance stay in ``core.config``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WarmingSettings(BaseSettings):
    """Tunables for the warming engine — all delays/limits live here, no magic numbers."""

    model_config = SettingsConfigDict(env_prefix="WARMING__", extra="ignore")

    action_delay_min_seconds: float = Field(default=10.0, ge=0.0)
    action_delay_max_seconds: float = Field(default=30.0, ge=0.0)
    typing_min_seconds: float = Field(default=5.0, ge=0.0)
    typing_max_seconds: float = Field(default=30.0, ge=0.0)
    reading_min_seconds: float = Field(default=8.0, ge=0.0)
    reading_max_seconds: float = Field(default=45.0, ge=0.0)
    # Fallback sleep when Telegram signals a FloodWait with no duration attached
    # (a full cool-down rather than an immediate retry of the just-flooded account).
    flood_wait_fallback_hours: float = Field(default=24.0, ge=0.0)
    startup_jitter_max_seconds: float = Field(default=8.0, ge=0.0)
    channels_per_cycle_min: int = Field(default=1, ge=1)
    channels_per_cycle_max: int = Field(default=3, ge=1)
    reaction_probability: float = Field(default=0.6, ge=0.0, le=1.0)
    read_message_limit: int = Field(default=15, ge=1, le=100)
    reaction_message_limit: int = Field(default=20, ge=1, le=100)
    # Telegram's reaction emoticons omit the U+FE0F variation selector (bare "❤",
    # not "❤️"); keep this set in that canonical form so it matches a channel's
    # allowed set. The reactor also strips FE0F defensively before comparing.
    default_reactions: list[str] = Field(
        default_factory=lambda: ["👍", "🔥", "❤", "😁", "🎉", "👏", "🤔", "🙏"],
    )
    # Emoji never used as a warming reaction. When a restrictive channel permits
    # none of ``default_reactions`` the reactor falls back to one of the channel's
    # own allowed emoji so a reaction still lands — but never a negative one.
    reaction_negative_emoji: list[str] = Field(
        default_factory=lambda: ["👎", "💩", "🤮", "🤬", "😡", "🖕", "🤢"],
    )
    # Channel guardrails. Service layer enforces these limits.
    max_channels_total: int = Field(default=500, ge=1)
    max_channels_per_add: int = Field(default=50, ge=1)
    max_channel_length: int = Field(default=120, ge=1)
    # Gemini DM payload guardrails — protect the recipient from junk output.
    chat_message_max_chars: int = Field(default=300, ge=1)
    chat_message_max_lines: int = Field(default=4, ge=1)
    # Graceful stop budget when cancelling a per-account loop task.
    stop_cancel_timeout_seconds: float = Field(default=5.0, ge=0.1)
    # Refuse to start warming an account that is not ready (dead session, no
    # proxy, no channels). Set False to bypass the pre-start gate.
    enforce_readiness: bool = True
    # Per-account daily action budget (joins+reads+reactions+messages). 0 = off.
    # When the day's count reaches the cap the account parks until the next daily
    # reset (UTC date rollover), shifted into its local active-hours window.
    max_daily_actions: int = Field(default=0, ge=0)
    # Watch subscribed channels' stories once per session (a low-risk, very human
    # signal). Applies to every persona; disable to skip the story-view step.
    story_view_enabled: bool = True
    # Jitter applied to the persona-derived inter-session gap: the even-spread gap
    # is multiplied by 1 ± this fraction so runs don't land on a rigid grid.
    next_run_jitter_fraction: float = Field(default=0.25, ge=0.0, le=1.0)
    # Cold-start guard: no outbound DM until the account is at least this old.
    dm_min_age_hours: float = Field(default=36.0, ge=0.0)
    # How long a cached @SpamBot verdict stays fresh before we re-probe. Frequent
    # /start to @SpamBot is itself suspicious, so keep this generous.
    spam_status_ttl_hours: float = Field(default=36.0, ge=0.0)
    # PEER_FLOOD quarantine: how long an account rests before its status is
    # re-checked, and how many consecutive still-limited re-checks are tolerated
    # before it is given up on (marked error + alerted).
    quarantine_hours: float = Field(default=48.0, gt=0.0)
    quarantine_max_repeats: int = Field(default=3, ge=1)
    # Content anti-repeat: refuse to send the same normalised text twice within
    # this window (identical content across accounts is a strong spam signal),
    # how many times to regenerate before giving up, plus an outbound filter.
    content_dedup_window_days: float = Field(default=7.0, ge=0.0)
    content_max_attempts: int = Field(default=3, ge=1)
    content_block_links: bool = True
    content_forbidden_words: list[str] = Field(
        default_factory=lambda: ["реклама", "купить", "продам", "продаю", "скидк", "промокод"],
    )
    # Retention windows for append-only tables that would otherwise grow forever.
    # 0 means "never purge" — kept as escape hatch.
    log_retention_days: float = Field(default=30.0, ge=0.0)
    dialogue_message_retention_days: float = Field(default=90.0, ge=0.0)
    sent_hash_retention_days: float = Field(default=14.0, ge=0.0)
    # How often the retention sweep runs while the process is up (a background
    # task alongside the per-account loops); a one-shot sweep at startup alone
    # lets the append-only tables grow unbounded during long uptimes.
    purge_interval_hours: float = Field(default=24.0, gt=0.0)
    # Inter-account dialogue pairing: how many partners each account gets, and
    # how often the acquaintance graph is reshuffled (imitates meeting people).
    dialogue_partners_min: int = Field(default=2, ge=1)
    dialogue_partners_max: int = Field(default=4, ge=1)
    dialogue_reshuffle_days: float = Field(default=10.0, gt=0.0)
    # A conversation fades after this many messages within the rolling window;
    # once the window passes the pair may start talking again (resumption).
    dialogue_max_turns: int = Field(default=12, ge=1)
    dialogue_conversation_window_hours: float = Field(default=48.0, gt=0.0)
    # Human-like pacing: inter-action pauses are drawn from a clipped log-normal
    # (heavy right tail — many short pauses, the occasional long one) instead of
    # a flat uniform, which is the most detectable timing pattern.
    delay_lognorm_mu: float = -0.8
    delay_lognorm_sigma: float = Field(default=0.6, gt=0.0)
    # Typing simulation: show the "typing…" action and wait a length-proportional
    # time before sending a DM (≈ WPM), clamped to a sane window.
    typing_simulation_enabled: bool = True
    typing_wpm: int = Field(default=45, ge=1)
    typing_sim_min_seconds: float = Field(default=0.5, ge=0.0)
    typing_sim_max_seconds: float = Field(default=12.0, ge=0.0)
    # Time-of-day cadence: bias the next cycle to land inside an active local-time
    # window (account's phone timezone), so accounts cluster activity in waking
    # hours instead of firing uniformly around the clock.
    active_hours_enabled: bool = True
    active_hours_start: int = Field(default=8, ge=0, le=23)
    active_hours_end: int = Field(default=23, ge=0, le=23)
    # How many of an account's most recent log rows the expandable per-card
    # activity panel on the warming board shows (newest-first).
    card_log_limit: int = Field(default=30, ge=1, le=200)


class GeminiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEMINI__", extra="ignore")

    api_key: str = ""
    model: str = Field(default="gemini-2.5-flash")
    base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta")
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.9, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=120, ge=1, le=2048)
    # Retry a transient failure (429 / 5xx / transport error) this many times
    # before surfacing it; the shared client is reused across calls so a hot-path
    # generate_text does not pay a fresh TLS handshake each time.
    max_retries: int = Field(default=1, ge=0, le=5)
    # Backoff slept between retries (seconds); kept short so the warming loop is
    # not blocked long on a flapping upstream.
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)


class OpenAISettings(BaseSettings):
    """Alternative captcha-solver LLM (OpenAI/ChatGPT).

    A separate key from Gemini, used only for challenge solving when the operator
    selects the ``openai`` provider. GPT vision handles image captchas well, so
    this is the recommended provider for the hardest challenges. The key is
    operator-set in the DB (falls back to ``OPENAI__API_KEY`` in .env).
    """

    model_config = SettingsConfigDict(env_prefix="OPENAI__", extra="ignore")

    api_key: str = ""
    model: str = Field(default="gpt-4o")
    base_url: str = Field(default="https://api.openai.com/v1")
    timeout_seconds: float = Field(default=30.0, ge=1.0)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=300, ge=1, le=2048)
    max_retries: int = Field(default=1, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=1.0, ge=0.0)


class TrustSettings(BaseSettings):
    """Tunables for the internal account Trust Score (our own metric, 0-100)."""

    model_config = SettingsConfigDict(env_prefix="TRUST__", extra="ignore")

    # Band lower bounds (score >= bound → that band, checked excellent → critical).
    excellent_min: int = Field(default=90, ge=0, le=100)
    good_min: int = Field(default=75, ge=0, le=100)
    watch_min: int = Field(default=60, ge=0, le=100)
    at_risk_min: int = Field(default=40, ge=0, le=100)
    # Penalties subtracted from a 100 baseline.
    penalty_not_alive: int = Field(default=40, ge=0, le=100)
    penalty_spam_limited: int = Field(default=50, ge=0, le=100)
    # "unknown" is absence of data (no @SpamBot probe yet / probe failed), not a
    # risk signal. Default 0 keeps the knob for operators who still want a nudge,
    # but the model no longer penalises uncertainty by default.
    penalty_spam_unknown: int = Field(default=0, ge=0, le=100)
    penalty_quarantine_each: int = Field(default=15, ge=0, le=100)
    penalty_flood_active: int = Field(default=15, ge=0, le=100)
    penalty_geo_mismatch: int = Field(default=10, ge=0, le=100)
    penalty_geo_unknown: int = Field(default=5, ge=0, le=100)
    penalty_proxy_failed: int = Field(default=20, ge=0, le=100)
    penalty_new_account: int = Field(default=10, ge=0, le=100)
    new_account_hours: float = Field(default=48.0, ge=0.0)


class NeurocommentSettings(BaseSettings):
    """Tunables for the neurocomment engine — pacing, caps, retries (no magic numbers)."""

    model_config = SettingsConfigDict(env_prefix="NEUROCOMMENT__", extra="ignore")

    # Human-like pause before replying to a fresh post.
    reply_delay_min_seconds: float = Field(default=3.0, ge=0.0)
    reply_delay_max_seconds: float = Field(default=10.0, ge=0.0)
    # Spacing between discussion-group joins at campaign onboarding.
    join_delay_min_seconds: float = Field(default=30.0, ge=0.0)
    join_delay_max_seconds: float = Field(default=60.0, ge=0.0)
    # Per-account throughput ceiling.
    max_comments_per_hour: int = Field(default=10, ge=1)
    # Minimum Trust Score an account needs to be picked for commenting (0 = no
    # gate). Operator-tunable via the neurocomment settings store + Settings UI.
    min_trust_score: int = Field(default=0, ge=0, le=100)
    # Comment length guardrail (words).
    comment_max_words: int = Field(default=30, ge=1)
    # Per-(account, channel) daily comment cap (0 = no cap).
    max_comments_per_channel_per_day: int = Field(default=3, ge=0)
    # Retries for a failed comment attempt before giving up.
    max_retries: int = Field(default=2, ge=0, le=5)
    # Cross-account semantic dedup (token-set Jaccard over normalized text): reject a
    # candidate whose max similarity to a recent posted comment in the same channel
    # within the window reaches this threshold, then regenerate. 0 disables it; the
    # exact-hash reservation stays the atomic claim regardless.
    semantic_dedup_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    # Look-back window for the semantic-dedup comparison set (recent posted comments).
    semantic_dedup_window_hours: float = Field(default=24.0, ge=0.0)
    # In-memory cooldown applied to an account after a PEER_FLOOD (no duration is
    # supplied by Telegram, unlike a timed flood-wait) before it is reselected.
    peer_flood_cooldown_seconds: float = Field(default=3600.0, ge=0.0)
    # A post whose text, stripped of links, leaves at most this many word chars is
    # treated as link-only / an ad and skipped.
    link_only_max_word_chars: int = Field(default=10, ge=0)
    # Grace period to await in-flight on-post tasks on shutdown before cancelling.
    stop_cancel_timeout_seconds: float = Field(default=5.0, ge=0.1)
    # Ф2 deletion-sweep → escalating channel back-off.
    # How often the periodic sweep re-reads recent comments (0 disables the sweep).
    deletion_sweep_interval_seconds: float = Field(default=1800.0, ge=0.0)
    # How far back the sweep re-checks posted comments for deletion.
    deletion_sweep_lookback_hours: float = Field(default=24.0, ge=0.0)
    # Vanished comments within the window needed to trip a channel's back-off.
    channel_backoff_min_deletions: int = Field(default=3, ge=1)
    # First back-off duration; doubles per consecutive trip, capped at the max.
    channel_backoff_base_seconds: float = Field(default=3600.0, ge=0.0)
    channel_backoff_max_seconds: float = Field(default=86400.0, ge=0.0)
    # Ф2 challenge solver — global default (a per-campaign solver_enabled overrides
    # it). Default ON so captcha solving is autonomous out of the box; turn it off
    # globally or per-campaign to fall back to the manual queue.
    challenge_solver_enabled: bool = True
    # Window the onboarding solver waits for a guardian-bot challenge after joining.
    challenge_wait_timeout_seconds: float = Field(default=20.0, gt=0.0)
    # Hard cutoff on the Gemini decision call.
    challenge_gemini_timeout_seconds: float = Field(default=10.0, gt=0.0)
    # Log-normal humanization pause before answering, clamped to [min, max]. Range
    # widened to ~human solve times (8-40s): instant/uniform solves read as a bot.
    challenge_click_delay_min_seconds: float = Field(default=8.0, ge=0.0)
    challenge_click_delay_max_seconds: float = Field(default=40.0, ge=0.0)
    # Default captcha-solver LLM (the operator overrides it via the DB setting).
    # "openai" uses settings.openai + the OpenAI key; "gemini" uses the Gemini one.
    challenge_llm_provider: Literal["gemini", "openai"] = "gemini"
    # Attempts before giving up: on a wrong answer the guardian bot usually
    # re-challenges, so we retry with the fresh challenge up to this many times
    # (a wrong click can get the account kicked — do not retry forever).
    challenge_max_attempts: int = Field(default=2, ge=1, le=5)
    # Short window to watch for a re-challenge after answering — a new challenge
    # means the previous answer was wrong (drives the retry); silence = passed.
    challenge_recheck_timeout_seconds: float = Field(default=8.0, gt=0.0)
    # Ф2 #147 channel challenge back-off: K consecutive solver failures on a channel
    # trip an escalating cooldown that stops onboarding new accounts there.
    channel_challenge_backoff_min_failures: int = Field(default=3, ge=1)
    channel_challenge_backoff_base_seconds: float = Field(default=3600.0, ge=0.0)
    channel_challenge_backoff_max_seconds: float = Field(default=86400.0, ge=0.0)
    # Minimum warming age (whole days) for an account to count as "warmed" in the
    # neurocomment page's top overview field.
    warmed_min_days: int = Field(default=14, ge=1)
    # Rows shown in the engine panel's collapsible neurocomment-activity log.
    log_limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> NeurocommentSettings:
        if self.reply_delay_min_seconds > self.reply_delay_max_seconds:
            msg = "reply_delay_min_seconds must not exceed reply_delay_max_seconds"
            raise ValueError(msg)
        if self.join_delay_min_seconds > self.join_delay_max_seconds:
            msg = "join_delay_min_seconds must not exceed join_delay_max_seconds"
            raise ValueError(msg)
        if self.challenge_click_delay_min_seconds > self.challenge_click_delay_max_seconds:
            msg = "challenge_click_delay_min_seconds must not exceed _max_seconds"
            raise ValueError(msg)
        if self.channel_challenge_backoff_base_seconds > self.channel_challenge_backoff_max_seconds:
            msg = "channel_challenge_backoff_base_seconds must not exceed _max_seconds"
            raise ValueError(msg)
        return self
