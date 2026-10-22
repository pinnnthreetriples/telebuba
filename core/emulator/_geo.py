"""The container's region: its proxy country's timezone and mobile carrier, its own language.

The timezone and the carrier follow the account's proxy country, so the phone's local
time and SIM agree with the IP Telegram sees. The language does not: it is the mobile
fingerprint's ``system_lang_code``, the one the account's sessions already announce,
because the app inside the container sends the phone's language as that same field.

The carrier is one of the country's real networks (``configs/carriers_mcc_mnc.json``),
picked from the account id so one account keeps one carrier; a country the table does
not list gets none, like a phone without a SIM.
"""

from __future__ import annotations

import hashlib
import json
from functools import cache
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, Field

from core.country_zones import ZONE_BY_COUNTRY
from core.emulator._blueprints import CONFIGS_DIR

_CARRIERS_PATH = CONFIGS_DIR / "carriers_mcc_mnc.json"
# The web window's fallback as well: a zero offset is no contradiction.
_DEFAULT_TIMEZONE = "Etc/UTC"

COUNTRY_COORDINATES: dict[str, tuple[float, float]] = {
    "BR": (-14.235, -51.9253),
    "BY": (53.7098, 27.9534),
    "DE": (51.1657, 10.4515),
    "ES": (40.4637, -3.7492),
    "FR": (46.2276, 2.2137),
    "GB": (55.3781, -3.436),
    "ID": (-0.7893, 113.9213),
    "IN": (20.5937, 78.9629),
    "IT": (41.8719, 12.5674),
    "KZ": (48.0196, 66.9237),
    "MX": (23.6345, -102.5528),
    "NL": (52.1326, 5.2913),
    "PL": (51.9194, 19.1451),
    "RU": (55.7558, 37.6173),
    "TR": (38.9637, 35.2433),
    "UA": (48.3794, 31.1656),
    "US": (37.0902, -95.7129),
}


class Carrier(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mcc: str = Field(pattern=r"^\d{3}$")
    mnc: str = Field(pattern=r"^\d{2,3}$")
    alpha: str = Field(pattern=r"^[A-Za-z0-9 .:&()-]+$")

    @property
    def numeric(self) -> str:
        return self.mcc + self.mnc


class Region(NamedTuple):
    # ISO-3166 alpha-2 (lower case, as ``gsm.*.iso-country``), ``None`` when unknown.
    country: str | None
    timezone: str
    locale: str
    carrier: Carrier | None


@cache
def load_carriers() -> dict[str, tuple[Carrier, ...]]:
    """Country (upper case) -> its carriers; read once per process."""
    raw = json.loads(_CARRIERS_PATH.read_text(encoding="utf-8"))
    return {
        country: tuple(Carrier.model_validate(entry) for entry in carriers)
        for country, carriers in raw.items()
    }


def region_for(country: str | None, locale: str, *, seed: str) -> Region:
    """The region of a container whose proxy is in ``country``, speaking ``locale``."""
    code = (country or "").upper()
    carriers = load_carriers().get(code, ())
    carrier = None
    if carriers:
        digest = hashlib.sha256(f"carrier:{seed}:{code}".encode()).digest()
        carrier = carriers[int.from_bytes(digest[:4], "big") % len(carriers)]
    return Region(
        country=code.lower() or None,
        timezone=ZONE_BY_COUNTRY.get(code, _DEFAULT_TIMEZONE),
        locale=locale,
        carrier=carrier,
    )
