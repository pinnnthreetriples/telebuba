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
    # Minimum spacing between Gemini calls (seconds); 0 = no throttle. The default
    # seeds the operator-editable settings-row column and is the gateway fallback
    # when a request does not carry its own override.
    min_interval_seconds: float = Field(default=0.0, ge=0.0)


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
    # Spacing between channel joins — campaign onboarding AND the listener
    # reconcile loop. Jittered anti-ban pause so a batch of joins never fires
    # as one burst (a Telegram freeze vector).
    join_delay_min_seconds: float = Field(default=30.0, ge=0.0)
    join_delay_max_seconds: float = Field(default=120.0, ge=0.0)
    # Per-account rolling-24h channel-join cap (0 = no cap). Telegram freezes an
    # account after roughly 20-50 channel joins a day, so both join sites (campaign
    # onboarding + the listener reconcile) skip further joins for an account once it
    # hits this; skipped joins resume as the window rolls / on the next reconcile.
    # 20 is a conservative safe default.
    max_joins_per_account_per_day: int = Field(default=20, ge=0)
    # Per-account throughput ceiling.
    max_comments_per_hour: int = Field(default=10, ge=1)
    # Cap on how many recent posted comments the board's published-comments feed
    # carries (newest first) — bounds the board payload for a busy campaign.
    board_comment_feed_limit: int = Field(default=50, ge=1)
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
    # L4: cap on concurrently in-flight on-post handler tasks (excess dropped under flood).
    max_concurrent_post_tasks: int = Field(default=50, ge=1)
    # L3: startup reclaim of claims stuck 'claimed' older than this.
    stale_claim_reclaim_seconds: float = Field(default=900.0, gt=0.0)
    # Ф2 deletion-sweep → escalating channel back-off.
    # How often the periodic sweep re-reads recent comments (0 disables the sweep).
    # 5 min → near-real-time deletion detection without hammering the read path.
    deletion_sweep_interval_seconds: float = Field(default=300.0, ge=0.0)
    # How far back the sweep re-checks posted comments for deletion.
    deletion_sweep_lookback_hours: float = Field(default=24.0, ge=0.0)
    # Vanished comments within the window needed to trip a channel's back-off.
    channel_backoff_min_deletions: int = Field(default=3, ge=1)
    # First back-off duration; doubles per consecutive trip, capped at the max.
    channel_backoff_base_seconds: float = Field(default=3600.0, ge=0.0)
    channel_backoff_max_seconds: float = Field(default=86400.0, ge=0.0)
    # Max concurrent Telegram ban probes for the "Проверить каналы" check — keeps
    # a burst of GetParticipant reads on a few accounts from tripping flood limits.
    ban_check_concurrency: int = Field(default=4, ge=1, le=32)
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
    # M4: min Gemini confidence for the solver to act; below → give_up.
    challenge_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # C1: case-insensitive REGEX fragments; the solver refuses to click a button whose label
    # matches any.
    challenge_button_denylist_patterns: list[str] = Field(
        default_factory=lambda: [
            "pay\\b",
            "payment",
            "оплат",
            "плат",
            "donat",
            "withdraw",
            "deposit",
            "wallet",
            "кошел",
            "bank",
            "card",
            "карт",
            "admin",
            "админ",
            "\\bvote\\b",
            "голос",
            "log ?in",
            "sign ?in",
            "войти",
            "вход",
            "password",
            "пароль",
            "invite",
            "claim",
            "airdrop",
            "bonus",
            "crypto",
            "authoriz",
            "authoris",
        ]
    )
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
