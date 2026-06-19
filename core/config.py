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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class UiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UI__", extra="ignore")

    port: int = Field(default=8080, ge=1, le=65535)
    # NiceGUI WS reconnect grace. Default 3 s is too tight when Telethon
    # uploads 5+ MB media — pyaes block on the loop trips the "Connection
    # lost" popup. 30 s rides over typical upload bursts.
    reconnect_timeout: float = Field(default=30.0, gt=0)


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
    # Quiet hours (UTC): when enabled, an account performs no actions inside the
    # [start, end) hour window and parks until it ends. start == end disables it.
    quiet_hours_enabled: bool = False
    quiet_hours_start: int = Field(default=0, ge=0, le=23)
    quiet_hours_end: int = Field(default=0, ge=0, le=23)
    # Per-account daily action budget (joins+reads+reactions+messages). 0 = off.
    # When the day's count reaches the cap the account parks until UTC midnight.
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    ui: UiSettings = Field(default_factory=UiSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    profile_media: ProfileMediaSettings = Field(default_factory=ProfileMediaSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    warming: WarmingSettings = Field(default_factory=WarmingSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    trust: TrustSettings = Field(default_factory=TrustSettings)


def load_settings() -> Settings:
    """Load + validate settings. Each nested model reads its own env prefix."""
    # Loading .env happens once via pydantic-settings dotenv source when present.
    # We still trigger an explicit dotenv load to support the case where the test
    # suite mutates os.environ after import (matches the pre-refactor behaviour).
    from dotenv import load_dotenv  # noqa: PLC0415 - keep import-time side-effects bounded.

    load_dotenv(override=False)
    return Settings()


settings = load_settings()
