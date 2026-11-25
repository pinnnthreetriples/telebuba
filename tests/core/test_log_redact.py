"""``scrub_extra`` and the ``log_event`` / ``signal_event`` call sites that apply it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from core import events
from core._log_redact import REDACTED, TRUNCATED, scrub_extra
from core.config import settings
from core.db import configure_database, list_recent_logs
from core.logging import log_event, reset_logging_for_tests, setup_logging, signal_event

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "Password",
        "twofa_password",
        "new-password",
        "passwd",
        "client_secret",
        "token",
        "bot_token",
        "access-token",
        "api_hash",
        "telegram_api_hash",
        "auth_key",
        "session_string",
        "credentials",
        "cookie",
        "set_cookie",
        "authorization",
        "login_code",
        "phone_code",
        "otp",
        "apiHash",
        "passwords",
    ],
)
def test_a_value_under_a_secret_key_is_redacted(key: str) -> None:
    assert scrub_extra({key: "s3cr3t-value"}) == {key: REDACTED}
    assert scrub_extra({key: b"s3cr3t-value"}) == {key: REDACTED}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("twofa_stored", True),
        ("password_set", False),
        ("token_count", 3),
        ("has_bot_token", True),
        ("token", None),
        ("password", ""),
        ("code", "session_dead"),
        ("country_code", "DE"),
        ("hash", "abc123"),
        ("hash_suffix", "9f2c"),
        ("old_hash_suffix", "1d4e"),
        ("session_file", "acc-1.session"),
        ("session_name", "acc-1"),
        ("keywords", "promo"),
        ("account", "tokenizer"),
        ("error", "flood_wait"),
    ],
)
def test_flags_error_codes_and_benign_keys_pass_through(key: str, value: object) -> None:
    assert scrub_extra({key: value}) == {key: value}


def test_nested_dicts_and_lists_are_scrubbed_recursively() -> None:
    payload = {
        "account": {"id": "a1", "twofa": {"password": "hunter2", "stored": True}},
        "attempts": [
            {"phone_code": "12345", "ok": False},
            {"label": "second", "token": "abc"},
        ],
        "plain": ("x", {"secret": "y"}),
    }

    assert scrub_extra(payload) == {
        "account": {"id": "a1", "twofa": {"password": REDACTED, "stored": True}},
        "attempts": [
            {"phone_code": REDACTED, "ok": False},
            {"label": "second", "token": REDACTED},
        ],
        "plain": ("x", {"secret": REDACTED}),
    }


def test_everything_textual_under_a_secret_key_is_redacted_even_in_containers() -> None:
    assert scrub_extra({"tokens": ["a", "b"], "credentials": {"user": "u", "n": 1}}) == {
        "tokens": [REDACTED, REDACTED],
        "credentials": {"user": REDACTED, "n": 1},
    }


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("socks5://user:pass@proxy.example:1080", "socks5://***@proxy.example:1080"),
        ("http://user:p@ss@proxy.example:8080/x", "http://***@proxy.example:8080/x"),
        (
            "connect failed via socks5://u:p@10.0.0.1:1080 timeout",
            "connect failed via socks5://***@10.0.0.1:1080 timeout",
        ),
        ("https://apikeyonly@host.example/path", "https://***@host.example/path"),
        ("no url here, mail me at a@b.example", "no url here, mail me at a@b.example"),
        ("https://host.example/path?e=a@b.example", "https://host.example/path?e=a@b.example"),
    ],
)
def test_url_userinfo_is_masked_in_any_string_whatever_its_key(raw: str, masked: str) -> None:
    assert scrub_extra({"error": raw, "nested": {"proxy": [raw]}}) == {
        "error": masked,
        "nested": {"proxy": [masked]},
    }


def test_the_input_is_not_mutated() -> None:
    payload: dict[str, object] = {"password": "x", "inner": {"token": "y"}}
    scrub_extra(payload)
    assert payload == {"password": "x", "inner": {"token": "y"}}


def test_recursion_is_depth_limited() -> None:
    deep: dict[str, object] = {"leaf": "socks5://u:p@h:1"}
    for _ in range(50):
        deep = {"n": deep}

    result: Any = scrub_extra(deep)
    for _ in range(50):
        result = result["n"]
        if result == TRUNCATED:
            break
    assert result == TRUNCATED


@pytest.fixture
def _isolated_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    configure_database(tmp_path / "logs.db")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.mark.usefixtures("_isolated_logging")
@pytest.mark.asyncio
async def test_log_event_persists_and_publishes_the_scrubbed_extra() -> None:
    async with events.subscribe() as queue:
        await log_event(
            "ERROR",
            "proxy_failed",
            account_id="acc-1",
            extra={
                "twofa_password": "hunter2",
                "twofa_stored": True,
                "error": "failed via socks5://user:pw@proxy.example:1080",
                "code": "session_dead",
                "attempts": [{"otp": "999"}],
            },
        )
        published = queue.get_nowait()

    expected = {
        "twofa_password": REDACTED,
        "twofa_stored": True,
        "error": "failed via socks5://***@proxy.example:1080",
        "code": "session_dead",
        "attempts": [{"otp": REDACTED}],
    }
    (stored,) = await list_recent_logs(1)
    assert stored.extra == expected
    assert published.extra == expected


@pytest.mark.usefixtures("_isolated_logging")
@pytest.mark.asyncio
async def test_signal_event_scrubs_what_it_fans_out() -> None:
    async with events.subscribe() as queue:
        signal_event("board_refresh", {"token": "abc", "count": 2})
        published = queue.get_nowait()

    assert published.extra == {"token": REDACTED, "count": 2}
