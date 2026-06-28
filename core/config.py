"""Project settings — typed pydantic-settings, one nested namespace per domain.

Env keys use a double-underscore separator: ``TELEGRAM__API_ID``,
``LOGGING__SENTRY_DSN``, ``WARMING__REACTION_PROBABILITY``, etc.

Validation runs at import time. A misconfigured ``.env`` raises a clear
``ValidationError`` instead of producing a half-initialised app with silent
defaults.

See ``.env.example`` for the full list of supported keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# RFC 7518 §3.2: an HS256 HMAC key should be at least 32 bytes.
_MIN_AUTH_SECRET_BYTES = 32


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TELEGRAM__", extra="ignore")

    api_id: int = Field(default=0, ge=0)
    api_hash: str = ""
    session_dir: Path = Path("sessions")
    timeout_seconds: int = Field(default=20, ge=1)
    connection_retries: int = Field(default=3, ge=0)
    retry_delay_seconds: int = Field(default=2, ge=0)
    request_retries: int = Field(default=3, ge=0)
    # Telethon auto-sleeps and retries on a flood wait whose duration is at or
    # below this threshold; longer waits raise so we can handle them ourselves.
    # Default 0 = surface every flood event to our own state machine (no silent
    # auto-sleep) so flood-type differentiation and quarantine logic always see it.
    flood_sleep_threshold: int = Field(default=0, ge=0)


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API__", extra="ignore")

    # 127.0.0.1 is the safe default (loopback only); set 0.0.0.0 via env for a
    # container/prod deploy. Binding loopback by default also avoids bandit B104.
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    # Allowed CORS origins for the SPA (dev usually uses the Vite proxy, so this
    # stays empty; set the deployed frontend origin when serving cross-origin).
    cors_origins: list[str] = Field(default_factory=list)
    # API path version segment (``/api/{version}``).
    version: str = Field(default="v1", min_length=1)


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH__", extra="ignore")

    # JWT signing secret — MUST be set to a long random value before enabling
    # login. Empty disables auth issuance (login returns 503-class refusal).
    secret: str = ""
    algorithm: str = Field(default="HS256", min_length=1)
    # Session cookie: HttpOnly is always on; Secure/SameSite are configurable so
    # local http dev can relax Secure. Sliding TTL re-issued on each request.
    cookie_name: str = Field(default="tb_session", min_length=1)
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_ttl_minutes: int = Field(default=720, ge=1)
    # First-admin seeding (no public signup): when no users exist and both are
    # set, an admin is created at startup.
    admin_username: str = ""
    admin_password: str = ""
    # Login brute-force guard (in-memory, per-process).
    login_rate_limit_max_attempts: int = Field(default=5, ge=1)
    login_rate_limit_window_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _check_secret_strength(self) -> AuthSettings:
        # Empty secret = auth disabled (login refuses 503). When set, require a
        # 32-byte HMAC key (RFC 7518 §3.2 for HS256) — a hard guarantee instead
        # of PyJWT's runtime warning.
        if self.secret and len(self.secret.encode("utf-8")) < _MIN_AUTH_SECRET_BYTES:
            msg = "AUTH__SECRET must be at least 32 bytes when set"
            raise ValueError(msg)
        return self


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB__", extra="ignore")

    path: Path = Path("telebuba.db")


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROXY__", extra="ignore")

    check_host: str = Field(default="ip-api.com", min_length=1)
    check_path: str = Field(default="/json?fields=status,message,query,country,countryCode,as")
    check_port: int = Field(default=80, ge=1, le=65535)
    check_timeout_seconds: float = Field(default=8.0, gt=0)
    # Substrings (case-insensitive) in the ASN string that mark a hosting /
    # datacenter network — lower trust than residential/mobile. ip-api's free
    # endpoint only exposes the ``as`` string, so we classify by known names.
    datacenter_asn_keywords: list[str] = Field(
        default_factory=lambda: [
            "amazon",
            "aws",
            "google",
            "microsoft",
            "azure",
            "digitalocean",
            "hetzner",
            "ovh",
            "linode",
            "vultr",
            "contabo",
            "leaseweb",
            "m247",
            "choopa",
            "oracle",
            "scaleway",
        ],
    )


class ProfileMediaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFILE_MEDIA__", extra="ignore")

    photo_max_bytes: int = Field(default=10_000_000, ge=1)
    story_image_max_bytes: int = Field(default=10_000_000, ge=1)
    story_video_max_bytes: int = Field(default=100_000_000, ge=1)
    music_max_bytes: int = Field(default=30_000_000, ge=1)
    # .session files = effective credentials. Cap to deter accidental large uploads.
    session_max_bytes: int = Field(default=5_000_000, ge=1)
    # How long a live-fetched profile snapshot is reused before the next
    # dialog-open triggers another GetFullUserRequest. 5 min keeps repeated
    # opens cheap without staling so much that a user sees outdated data.
    read_snapshot_ttl_seconds: int = Field(default=300, ge=1)
    # Max tracks pulled by the profile-music preview. Low cap keeps the TL
    # response light — the tab is a preview list, not a media library.
    music_preview_limit: int = Field(default=50, ge=1, le=200)


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOGGING__", extra="ignore")

    path: Path = Path("debug.log")
    level: str = Field(default="INFO")
    rotation: str = Field(default="10 MB")
    retention: int = Field(default=10, ge=1)
    sentry_dsn: str = ""


class WarmingSettings(BaseSettings):
    """Tunables for the warming engine — all delays/limits live here, no magic numbers."""

    model_config = SettingsConfigDict(env_prefix="WARMING__", extra="ignore")

    action_delay_min_seconds: float = Field(default=10.0, ge=0.0)
    action_delay_max_seconds: float = Field(default=30.0, ge=0.0)
    typing_min_seconds: float = Field(default=5.0, ge=0.0)
    typing_max_seconds: float = Field(default=30.0, ge=0.0)
    reading_min_seconds: float = Field(default=8.0, ge=0.0)
    reading_max_seconds: float = Field(default=45.0, ge=0.0)
    cycle_sleep_min_hours: float = Field(default=12.0, ge=0.0)
    cycle_sleep_max_hours: float = Field(default=30.0, ge=0.0)
    startup_jitter_max_seconds: float = Field(default=8.0, ge=0.0)
    channels_per_cycle_min: int = Field(default=1, ge=1)
    channels_per_cycle_max: int = Field(default=3, ge=1)
    reaction_probability: float = Field(default=0.6, ge=0.0, le=1.0)
    read_message_limit: int = Field(default=15, ge=1, le=100)
    reaction_message_limit: int = Field(default=20, ge=1, le=100)
    default_reactions: list[str] = Field(
        default_factory=lambda: ["👍", "🔥", "❤️", "😁", "🎉", "👏", "🤔", "🙏"],
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
    # Quiet hours (account-local time, from the phone's timezone): when enabled,
    # an account performs no actions inside the [start, end) hour window and
    # parks until it ends. start == end disables it.
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = Field(default=0, ge=0, le=23)
    quiet_hours_end: int = Field(default=0, ge=0, le=23)
    # Per-account daily action budget (joins+reads+reactions+messages). 0 = off.
    # When the day's count reaches the cap the account parks until the next daily
    # reset (UTC date rollover), shifted into its local active-hours window.
    max_daily_actions: int = Field(default=0, ge=0)
    # Age-based ramp ("balanced" profile): a fresh account behaves quietly and
    # grows to full intensity over ``ramp_full_age_hours``. Disable to make every
    # account run at full intensity from day one.
    ramp_enabled: bool = True
    ramp_full_age_hours: float = Field(default=192.0, ge=0.0)
    ramp_initial_channels_max: int = Field(default=1, ge=1)
    ramp_initial_reaction_probability: float = Field(default=0.1, ge=0.0, le=1.0)
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
    # Ф2 challenge solver — global opt-in flag (default off; a per-campaign
    # solver_enabled overrides it). The solver costs Gemini tokens and clicks in
    # live chats, so it does not auto-activate on deploy (mirrors #132's pattern).
    challenge_solver_enabled: bool = False
    # Window the onboarding solver waits for a guardian-bot challenge after joining.
    challenge_wait_timeout_seconds: float = Field(default=20.0, gt=0.0)
    # Hard cutoff on the Gemini decision call.
    challenge_gemini_timeout_seconds: float = Field(default=10.0, gt=0.0)
    # Log-normal humanization pause before clicking, clamped to [min, max].
    challenge_click_delay_min_seconds: float = Field(default=3.0, ge=0.0)
    challenge_click_delay_max_seconds: float = Field(default=6.0, ge=0.0)
    # Reserved for Phase-2 human-queue routing of low-confidence decisions.
    challenge_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    profile_media: ProfileMediaSettings = Field(default_factory=ProfileMediaSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    warming: WarmingSettings = Field(default_factory=WarmingSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    trust: TrustSettings = Field(default_factory=TrustSettings)
    neurocomment: NeurocommentSettings = Field(default_factory=NeurocommentSettings)


def load_settings() -> Settings:
    """Load + validate settings. Each nested model reads its own env prefix."""
    # Loading .env happens once via pydantic-settings dotenv source when present.
    # We still trigger an explicit dotenv load to support the case where the test
    # suite mutates os.environ after import (matches the pre-refactor behaviour).
    from dotenv import load_dotenv  # noqa: PLC0415 - keep import-time side-effects bounded.

    load_dotenv(override=False)
    return Settings()


settings = load_settings()
