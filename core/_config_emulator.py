"""The local Android container (redroid under Docker) a mobile account can be opened in.

Split out of ``core.config`` for the file-size budget; re-exported there.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# A word ``input text`` can type into the app's search: letters, digits and spaces.
_Typeable = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ]{1,31}$")]


class EmulatorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMULATOR__", extra="ignore")

    # Off unless the operator turns it on: the open-mobile endpoint then answers
    # ``mobile_emulator_disabled`` and the button stays disabled.
    enabled: bool = False
    # redroid needs a kernel with binder (docs/redroid.md). Point ``adb_path`` at the
    # adb scrcpy ships with: two adb versions on one host keep killing each other's server.
    adb_path: Path = Path("adb")
    # The docker CLI, the scrcpy viewer, the image per Android SDK, and the host port
    # of container index 0's adb (index N listens on that port + N, on 127.0.0.1 only).
    docker_path: Path = Path("docker")
    scrcpy_path: Path = Path("scrcpy")
    # The desktop scrcpy window on every open. Off by default: it encodes video for as
    # long as it is open (warming stints included); the web screen does that on demand.
    desktop_viewer: bool = False
    # SDK -> image on an amd64 (or any non-ARM64) Docker host: the tags with a 32-bit
    # userspace, whose OMX service holds the only video encoders (the 64-bit-only tags
    # list none, so scrcpy cannot stream their screen).
    redroid_images: dict[int, str] = Field(
        default_factory=lambda: {
            33: "redroid/redroid:13.0.0-latest",
            34: "redroid/redroid:14.0.0-latest",
        }
    )
    # The same on an ARM64 host: 64-bit-only tags, since servers without 32-bit EL0
    # (Ampere) cannot boot the images that carry a 32-bit userspace.
    redroid_images_arm64: dict[int, str] = Field(
        default_factory=lambda: {
            33: "redroid/redroid:13.0.0_64only-latest",
            34: "redroid/redroid:14.0.0_64only-latest",
        }
    )
    # How Android draws: ``guest`` in software (any host), ``host`` on the host's GPU
    # through /dev/dri (refused when the host has none), ``auto`` the GPU when there is
    # one. Fixed at a container's create: a container made before keeps its mode.
    gpu_mode: Literal["guest", "host", "auto"] = "guest"
    # The transport Android files eth0 under (``core.emulator._transport``): ``cellular``
    # matches the phone's SIM and LTE, ``ethernet`` is AOSP's own label. Written before
    # every cold boot, so a refresh applies a change.
    network_transport: Literal["cellular", "ethernet"] = "cellular"
    redroid_first_adb_port: int = Field(default=5600, ge=1024, le=65000)
    # Android's /data as overlayfs (docs/redroid.md, Shared /data): ``<base>/<sdk>`` is
    # the read-only lower layer the operator prepared, ``<diff>/<container>`` the pod's
    # own. Both absolute paths on the Docker host, or neither: /data then lives in the
    # container. Fixed at a container's create, like ``gpu_mode``.
    data_base_dir: Path | None = None
    data_diff_dir: Path | None = None
    # The pod's network side, built from docker/net-gateway (docs/redroid.md), and the
    # bridge network its gateways join (created when missing, containers isolated).
    gateway_image: str = Field(default="telebuba/net-gateway:2", min_length=1)
    gateway_network: str = Field(default="telebuba-emulator", min_length=1)
    # Installed into a container with no Telegram; a missing file is fetched from
    # telegram.org, kept only when Telegram signed it (``core.emulator.assets``).
    telegram_apk_path: Path = Path("emulator/telegram.apk")
    # Directory storing versioned APKs (<store_dir>/<package>/<version>.apk).
    apk_store_dir: Path = Path("emulator/apk_store")
    # The Telegram a container already has is driven: this package first, else the APK's.
    telegram_package: str = Field(default="org.telegram.messenger", min_length=1)
    # That APK's "versionName (versionCode)": new mobile fingerprints claim it, so the
    # app in the container and the fingerprint agree. Empty: drawn from the catalogue.
    telegram_app_version: str = Field(default="", pattern=r"^(\d+\.\d+\.\d+ \(\d+\))?$")
    # Container name = prefix + account id, so a container is found again by name.
    instance_prefix: str = Field(default="telebuba-", min_length=1)
    # CPU cores and RAM of a new container (its screen is its phone's, ``configs/``).
    # A phone with a chip (``configs/soc``) gets that chip's cores instead of ``cpu_cores``,
    # and sees its topology, written under ``cpu_topology_dir`` (``core.emulator._topology``).
    cpu_cores: int = Field(default=2, ge=1, le=8)
    memory_mb: int = Field(default=2048, ge=1024, le=8192)
    cpu_topology_dir: Path = Path("emulator/cpu")
    # One ``power_supply`` tree per pod index, mounted for its battery HAL (``_battery``).
    battery_dir: Path = Path("emulator/battery")
    # ``docker build --output`` of docker/sensors-hal: ``<dir>/<abi>/`` holds the sensors
    # HAL a new or refreshed container gets (``core.emulator._sensors``). Empty: no sensors.
    sensors_hal_dir: Path | None = None
    # One docker/adb call; the gateway's tunnel to come up; an APK install; the Android
    # boot; one screen to appear; the login code to reach the account's existing
    # session; the screen poll interval.
    command_timeout_seconds: float = Field(default=60.0, gt=0)
    tunnel_timeout_seconds: float = Field(default=30.0, gt=0)
    install_timeout_seconds: float = Field(default=300.0, gt=0)
    # A missing image's ``docker pull`` (redroid) or ``docker build`` (the gateway).
    image_timeout_seconds: float = Field(default=1800.0, gt=0)
    boot_timeout_seconds: float = Field(default=180.0, gt=0)
    ui_timeout_seconds: float = Field(default=30.0, gt=0)
    code_wait_seconds: float = Field(default=90.0, gt=0)
    poll_seconds: float = Field(default=2.0, gt=0)
    # The web screen (``core.emulator.scrcpy``): the scrcpy-server file pushed to the
    # phone and its EXACT version (the SPA's decoder speaks that protocol, 3.3.1 at most);
    # the longest video side, frame rate and bit rate it encodes at; how long the server
    # may take to accept its sockets.
    scrcpy_server_path: Path = Path("emulator/scrcpy-server-v3.3.1")
    scrcpy_server_version: str = Field(default="3.3.1", pattern=r"^\d+\.\d+(\.\d+)?$")
    # A missing scrcpy-server is fetched from that version's GitHub release and kept
    # only when its sha256 is this one; a version change needs its own hash here.
    scrcpy_server_sha256: str = Field(
        default="a0f70b20aa4998fbf658c94118cd6c8dab6abbb0647a3bdab344d70bc1ebcbb8",
        pattern=r"^[0-9a-f]{64}$",
    )
    # One asset download from its official source, start to finish.
    download_timeout_seconds: float = Field(default=600.0, gt=0)
    stream_max_size: int = Field(default=1280, ge=320, le=4096)
    stream_max_fps: int = Field(default=30, ge=5, le=60)
    stream_bit_rate: int = Field(default=4_000_000, ge=500_000, le=20_000_000)
    stream_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    # Longest activity session (``run_mobile_activity``) in seconds.
    activity_max_seconds: int = Field(default=900, gt=0)
    # Pods warming may run at once; a mobile stint finding none free skips its cycle
    # (``emulator_capacity``) and tries again next time (the operator's opens are not counted).
    max_running_pods: int = Field(default=4, ge=1)
    # A warming pod is paused, not stopped, after a visit, and stopped once paused this
    # long: a visit within it skips the Android boot. 0 stops it after every visit.
    pause_ttl_seconds: int = Field(default=1200, ge=0)
    # Words a warming session may type into the app's global search, one a visit at
    # most: letters, digits and spaces only, as ``input text`` types nothing else.
    search_queries: list[_Typeable] = Field(
        min_length=1,
        default_factory=lambda: [
            "news",
            "music",
            "travel",
            "recipes",
            "football",
            "movies",
            "books",
            "weather",
        ],
    )
    # The same, per phone language (ISO 639-1) where its words can be typed; a phone
    # whose language is missing here (Cyrillic, say: ``input text`` cannot type it)
    # searches with ``search_queries``.
    search_queries_by_language: dict[str, list[_Typeable]] = Field(
        default_factory=lambda: {
            "de": ["nachrichten", "musik", "reisen", "rezepte", "fussball", "wetter"],
            "es": ["noticias", "musica", "viajes", "recetas", "futbol", "tiempo"],
            "fr": ["actualites", "musique", "voyage", "recettes", "football", "meteo"],
            "it": ["notizie", "musica", "viaggi", "ricette", "calcio", "meteo"],
            "pt": ["noticias", "musica", "viagens", "receitas", "futebol", "tempo"],
        },
    )

    @field_validator("data_base_dir", "data_diff_dir", "sensors_hal_dir", mode="before")
    @classmethod
    def _empty_is_off(cls, value: object) -> object:
        # ``EMULATOR__DATA_BASE_DIR=`` in .env is "off", not the working directory.
        return None if value == "" else value

    @model_validator(mode="after")
    def _check_data_layers(self) -> EmulatorSettings:
        layers = (self.data_base_dir, self.data_diff_dir)
        if (layers[0] is None) != (layers[1] is None):
            msg = "data_base_dir and data_diff_dir are set together or not at all"
            raise ValueError(msg)
        # A relative ``docker -v`` source is a named volume, not a directory.
        if any(
            layer is not None and not (layer.is_absolute() or layer.as_posix().startswith("/"))
            for layer in layers
        ):
            msg = "data_base_dir and data_diff_dir must be absolute paths"
            raise ValueError(msg)
        if any(layer is not None and ".." in layer.parts for layer in layers):
            msg = "data_base_dir and data_diff_dir must not contain '..'"
            raise ValueError(msg)
        # A pod's diff is removed as root with ``rm -rf``: never directly under ``/``.
        if self.data_diff_dir is not None and self.data_diff_dir.parent == self.data_diff_dir:
            msg = "data_diff_dir must not be the filesystem root"
            raise ValueError(msg)
        return self
