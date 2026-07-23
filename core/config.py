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

# The larger self-contained domains live in a sibling module for the file-size
# budget; re-exported here so ``from core.config import WarmingSettings`` etc.
# keep working unchanged.
from core._config_domains import (
    GeminiSettings,
    NeurocommentSettings,
    OpenAISettings,
    TrustSettings,
)
from core._config_warming import WarmingSettings

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
    # Phone-login: how long a requested SMS code's ``phone_code_hash`` stays
    # valid in the in-memory login cache before the operator must re-request.
    # Telegram codes themselves expire in a few minutes; 300 s mirrors that.
    phone_code_ttl_seconds: int = Field(default=300, ge=1)


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="API__", extra="ignore")

    # 127.0.0.1 is the safe default (loopback only); set 0.0.0.0 via env for a
    # container/prod deploy. Binding loopback by default also avoids bandit B104.
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1, le=65535)
    # Allowed CORS origins for the SPA (dev usually uses the Vite proxy, so this
    # stays empty; set the deployed frontend origin when serving cross-origin).
    cors_origins: list[str] = Field(default_factory=list)
    # Send Access-Control-Allow-Credentials. When True, ``cors_origins`` must not
    # be the ``"*"`` wildcard (a credentialed wildcard is a CORS hole — the
    # validator below rejects the combo).
    cors_allow_credentials: bool = True
    # Reverse-proxy trust: when True, uvicorn's ProxyHeadersMiddleware rewrites
    # ``request.client.host`` from X-Forwarded-For for connections originating
    # from ``forwarded_allow_ips`` (comma-separated, or "*"). Off by default so a
    # direct-exposed deploy never trusts a spoofable header. Never hand-parse XFF.
    trust_proxy_headers: bool = False
    forwarded_allow_ips: str = Field(default="127.0.0.1", min_length=1)
    # API path version segment (``/api/{version}``).
    version: str = Field(default="v1", min_length=1)
    # SSE live-event stream (``GET /api/v1/events``): keepalive comment cadence
    # (keeps idle proxies from closing the stream) + per-subscriber queue bound
    # (a slow client's queue fills → its live frames drop; the FE poll backstops).
    sse_keepalive_seconds: float = Field(default=15.0, gt=0)
    sse_max_queue: int = Field(default=1000, ge=1)

    @model_validator(mode="after")
    def _reject_credentialed_cors_wildcard(self) -> ApiSettings:
        # A wildcard origin with credentials would let any site read authed
        # responses; browsers forbid it and so do we (fail loud at startup).
        if self.cors_allow_credentials and "*" in self.cors_origins:
            msg = 'API__CORS_ORIGINS must not contain "*" when credentials are allowed'
            raise ValueError(msg)
        return self


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
    # Connection pool sizing. Every DB call runs under asyncio.to_thread (default
    # ThreadPoolExecutor up to min(32, cpu+4) workers), so the default QueuePool
    # (5+10) is easily oversubscribed under bursty logging → checkout stalls /
    # "database is locked". Size the pool to the executor's worst case instead.
    pool_size: int = Field(default=10, ge=1)
    max_overflow: int = Field(default=20, ge=0)
    pool_timeout_seconds: float = Field(default=30.0, gt=0)
    # Periodic maintenance: WAL checkpoint (TRUNCATE) always runs; the online
    # backup (VACUUM INTO a timestamped file) is opt-in. telebuba.db is the sole
    # datastore (incl. users/auth), so a backup guards against corruption/loss.
    backup_enabled: bool = False
    backup_interval_hours: float = Field(default=24.0, gt=0)
    backup_dir: Path = Path("backups")
    backup_keep: int = Field(default=7, ge=1)


class ProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROXY__", extra="ignore")

    # Pool capacity: how many accounts may share one proxy. The design's
    # "до N аккаунтов на прокси". Global (no per-proxy override — YAGNI).
    max_accounts_per_proxy: int = Field(default=3, ge=1)
    # Resolve the real proxy egress through a TLS-authenticated endpoint first.
    exit_ip_host: str = Field(default="api64.ipify.org", min_length=1)
    exit_ip_path: str = Field(default="/?format=json", min_length=1)
    exit_ip_port: int = Field(default=443, ge=1, le=65535)
    check_timeout_seconds: float = Field(default=8.0, gt=0)
    # Free country providers. IPinfo Lite has no query quota; GeoLite Country
    # allows 1,000 lookups/day. Empty credentials disable that provider without
    # turning a reachable proxy into a failed proxy.
    ipinfo_token: str = ""
    ipinfo_base_url: str = Field(default="https://api.ipinfo.io/lite", min_length=1)
    maxmind_account_id: str = ""
    maxmind_license_key: str = ""
    maxmind_base_url: str = Field(
        default="https://geolite.info/geoip/v2.1/country",
        min_length=1,
    )
    # IPinfo Lite exposes an ASN number/name but not a network-type flag, so
    # datacenter classification remains a conservative name-based signal.
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

    @model_validator(mode="after")
    def _require_https_geo_endpoints(self) -> ProxySettings:
        for field_name in ("ipinfo_base_url", "maxmind_base_url"):
            if not getattr(self, field_name).startswith("https://"):
                msg = f"PROXY__{field_name.upper()} must use HTTPS"
                raise ValueError(msg)
        return self


class ProfileMediaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROFILE_MEDIA__", extra="ignore")

    photo_max_bytes: int = Field(default=10_000_000, ge=1)
    story_image_max_bytes: int = Field(default=10_000_000, ge=1)
    story_video_max_bytes: int = Field(default=100_000_000, ge=1)
    # Multi-photo "collage" stories: hard cap on how many photos stitch into one
    # composite, and the gap (px) drawn between cells on the 1080x1920 canvas.
    story_collage_max_images: int = Field(default=6, ge=2, le=6)
    story_collage_gap_px: int = Field(default=8, ge=0)
    music_max_bytes: int = Field(default=30_000_000, ge=1)
    # Concurrent thumbnail downloads per read batch (photo history / stories).
    # Unbounded gather over up to 100 downloads hammered the DC in parallel and
    # tripped flood limits; 4 keeps the modal open fast without the stampede.
    thumb_concurrency: int = Field(default=4, ge=1)
    # .session files = effective credentials. Cap to deter accidental large uploads.
    session_max_bytes: int = Field(default=5_000_000, ge=1)
    # How long a live-fetched profile snapshot is reused before the next
    # dialog-open triggers another GetFullUserRequest. Kept short: the snapshot
    # is only invalidated by THIS app's edits, so a change made in the Telegram
    # app (or by another session) is invisible until the TTL lapses — a long
    # window made the modal show photos that no longer matched the real profile.
    # 30s still coalesces rapid reopens; «Обновить» forces an immediate re-pull.
    read_snapshot_ttl_seconds: int = Field(default=30, ge=1)
    # Max tracks pulled by the profile-music preview. Low cap keeps the TL
    # response light — the tab is a preview list, not a media library.
    music_preview_limit: int = Field(default=50, ge=1, le=200)
    # How deep to scan the profile-photo history when re-resolving a photo's
    # fresh InputPhoto for «make main». The target must be within this many
    # most-recent photos; 100 is Telegram's per-request ceiling.
    set_main_history_limit: int = Field(default=100, ge=1, le=100)
    # Hard cap on each ffmpeg subprocess (encode / thumbnail / duration probe).
    # A stalling or maliciously-crafted video would otherwise hang the request
    # coroutine forever and orphan the process; on timeout we kill it and fail.
    ffmpeg_timeout_seconds: float = Field(default=120.0, ge=1.0)
    # Browser cache lifetime for the per-item profile thumbnail endpoints. Thumbnails
    # are content-immutable per photo_id/story_id, so this can be long; served with
    # Cache-Control: private (per-account, authenticated) + an ETag for revalidation.
    thumb_cache_max_age_seconds: int = Field(default=3600, ge=0)


class ChannelsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHANNELS__", extra="ignore")

    avatar_max_bytes: int = Field(default=10_000_000, ge=1)
    post_photo_max_bytes: int = Field(default=10_000_000, ge=1)
    post_video_max_bytes: int = Field(default=100_000_000, ge=1)
    # Default page size for the channel-posts list endpoint.
    posts_page_limit: int = Field(default=20, ge=1, le=100)
    # Max own channels returned by the list endpoint (ceiling: the
    # ListOwnChannels action's le=200).
    list_limit: int = Field(default=100, ge=1, le=200)
    # How many dialogs to scan when discovering the account's own channels —
    # owned channels are found by filtering the dialog list (creator+broadcast),
    # so the scan depth bounds how far down an old channel can still be found.
    dialogs_scan_limit: int = Field(default=500, ge=1)


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOGGING__", extra="ignore")

    path: Path = Path("debug.log")
    level: str = Field(default="INFO")
    rotation: str = Field(default="10 MB")
    retention: int = Field(default=10, ge=1)
    sentry_dsn: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    profile_media: ProfileMediaSettings = Field(default_factory=ProfileMediaSettings)
    channels: ChannelsSettings = Field(default_factory=ChannelsSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    warming: WarmingSettings = Field(default_factory=WarmingSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
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
