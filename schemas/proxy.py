from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

ProxyType = Literal["socks5", "https"]
ProxyStatus = Literal["unknown", "tcp_working", "failed"]
GeoStatus = Literal["unknown", "single_source", "confirmed", "conflict", "unavailable"]
# Underscores are illegal in a *hostname* per RFC 1123 but legal in a DNS label, and
# commercial proxy vendors sell endpoints shaped like ``gate_1.smartproxy.com``.
# ``getaddrinfo`` resolves them, so rejecting them would lock working endpoints out of
# the pool. Only the interior is permissive: a leading or trailing ``_`` or ``-`` still
# fails, as does a label over 63 characters.
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?$")
_CONTROL_MAX = 32
_ASCII_DELETE = 127
_MAX_DNS_HOST_CHARS = 253


def _unbracket_host(host: str) -> tuple[str, bool]:
    bracketed = host.startswith("[") or host.endswith("]")
    if bracketed and not (host.startswith("[") and host.endswith("]")):
        msg = "Proxy IPv6 brackets are malformed"
        raise ValueError(msg)
    return (host[1:-1], True) if bracketed else (host, False)


def _canonical_ip(host: str, *, bracketed: bool) -> str | None:
    if "%" in host:
        msg = "Proxy host must not contain an IPv6 zone identifier"
        raise ValueError(msg)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if bracketed or ":" in host:
            msg = "Proxy host must not include a port or malformed IPv6 address"
            raise ValueError(msg) from None
        return None
    return address.compressed.lower()


def canonicalize_proxy_host(value: str) -> str:
    """Return one stable DNS/IP identity, rejecting URLs and host:port input."""
    if not isinstance(value, str):
        msg = "Proxy host must be a string"
        raise TypeError(msg)
    host = value.strip()
    if not host or any(
        ord(character) <= _CONTROL_MAX or ord(character) == _ASCII_DELETE for character in host
    ):
        msg = "Proxy host must not be blank or contain control characters"
        raise ValueError(msg)

    host, bracketed = _unbracket_host(host)
    if address := _canonical_ip(host, bracketed=bracketed):
        return address

    # A trailing root dot is equivalent to the same DNS name without it. Store a
    # single identity so re-adding it refreshes credentials rather than creating
    # an operational duplicate.
    hostname = host.rstrip(".")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        msg = "Proxy host is not a valid internationalized DNS name"
        raise ValueError(msg) from None
    labels = ascii_hostname.split(".")
    # An all-numeric name is a malformed IP, not a DNS name: ``999.999.999.999``, and
    # zero-padded forms such as ``10.0.0.010`` which ``inet_aton`` reads as octal (a
    # DIFFERENT address than the decimal reading). Refusing beats guessing which one
    # the operator meant and silently pinning traffic to the wrong host.
    if (
        not ascii_hostname
        or len(ascii_hostname) > _MAX_DNS_HOST_CHARS
        or all(label.isdigit() for label in labels)
        or any(not _DNS_LABEL.fullmatch(label) for label in labels)
    ):
        msg = "Proxy host must be a valid DNS name or IP address without a port"
        raise ValueError(msg)
    return ascii_hostname


ProxyHost = Annotated[
    str,
    BeforeValidator(canonicalize_proxy_host),
    Field(min_length=1, max_length=253),
]


class ProxyCreate(BaseModel):
    """Operator input when adding a proxy to the pool."""

    proxy_type: ProxyType
    # A host is an endpoint name, not a URL. Normalise it before it can become
    # part of the proxy identity or reach the repository transaction.
    host: ProxyHost
    port: int = Field(ge=1, le=65_535)
    username: str | None = Field(default=None, min_length=1)
    password: str | None = Field(default=None, min_length=1)


class ProxyRead(BaseModel):
    """A pool proxy as shown on the Accounts page (masked credentials)."""

    id: str = Field(min_length=1)
    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = None
    has_password: bool
    status: ProxyStatus
    last_checked_at: str | None = None
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    geo_status: GeoStatus = "unknown"
    ipinfo_country_code: str | None = None
    maxmind_country_code: str | None = None
    asn: str | None = None
    is_datacenter: bool = False
    created_at: str
    updated_at: str
    used: int = Field(ge=0)
    capacity: int = Field(ge=1)
    free: int = Field(ge=0)


class ProxyList(BaseModel):
    proxies: list[ProxyRead]


class ProxySettings(BaseModel):
    """Unmasked proxy credentials handed to the Telegram/connectivity gateways."""

    proxy_type: ProxyType
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65_535)
    username: str | None = None
    password: str | None = None


class ProxyAssignRequest(BaseModel):
    account_id: str = Field(min_length=1)


class ProxyCheckResult(BaseModel):
    status: ProxyStatus
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    geo_status: GeoStatus = "unknown"
    ipinfo_country_code: str | None = None
    maxmind_country_code: str | None = None
    asn: str | None = None
    is_datacenter: bool = False


class ProxyCheckUpdate(BaseModel):
    proxy_id: str = Field(min_length=1)
    status: ProxyStatus
    last_error: str | None = None
    exit_ip: str | None = None
    country_code: str | None = None
    country_name: str | None = None
    geo_status: GeoStatus = "unknown"
    ipinfo_country_code: str | None = None
    maxmind_country_code: str | None = None
    asn: str | None = None
    is_datacenter: bool = False
