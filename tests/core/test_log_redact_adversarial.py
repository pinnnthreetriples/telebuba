"""Adversarial checks of the journal scrubber (``core._log_redact``) and ``log_event``."""

from __future__ import annotations

import copy
import json
import time
from typing import TYPE_CHECKING

import pytest

from core._log_redact import REDACTED, scrub_extra
from core.config import settings
from core.db import configure_database, list_filtered_logs
from core.events import subscribe
from core.logging import log_event, reset_logging_for_tests, setup_logging
from schemas.logs import LogFilter

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_SECRET = "fake-value-" * 2  # filler, not a credential


@pytest.fixture
def _journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


def _dump(value: object) -> str:
    return json.dumps(value, default=repr)


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "PASSWORD",
        "Password",
        "twoFaPassword",
        "twofa_password",
        "x-auth-token",
        "X-Auth-Token",
        "authToken",
        "api_hash",
        "apiHash",
        "session_string",
        "sessionString",
        "login_code",
        "phone_code",
        "cookies",
        "Authorization",
        "otp",
        "client_secret",
        "access_token",
        "refresh-token",
    ],
)
def test_secret_named_keys_are_redacted(key: str) -> None:
    out = scrub_extra({key: _SECRET})
    assert out[key] == REDACTED


@pytest.mark.parametrize(
    "key",
    ["oldpassword", "newpassword", "userpassword", "csrftoken", "accesstoken", "pwd", "passphrase"],
)
def test_glued_secret_names_are_redacted(key: str) -> None:
    assert scrub_extra({key: _SECRET})[key] == REDACTED


# Bare ``auth``, ``twofa`` and ``key`` stay unredacted by decision (too common as plain
# labels); only the unambiguous credential names below are secrets.
@pytest.mark.parametrize("key", ["api_key", "apikey", "private_key", "bearer", "jwt", "otp"])
def test_other_credential_key_names_are_redacted(key: str) -> None:
    assert scrub_extra({key: _SECRET})[key] == REDACTED


def test_benign_values_survive() -> None:
    extra = {
        "code": "session_dead",
        "hash": "abc",
        "twofa_stored": True,
        "password_len": 12,
        "token_version": 3,
        "count": 0,
        "password": "",
        "tokens_used": 5,
        "none_value": None,
    }
    assert scrub_extra(extra) == extra


def test_secret_key_with_empty_or_flag_value_is_kept() -> None:
    assert scrub_extra({"password": ""}) == {"password": ""}
    assert scrub_extra({"password": True}) == {"password": True}


def test_integer_login_code_under_a_secret_key_is_redacted() -> None:
    assert scrub_extra({"login_code": 12345})["login_code"] == REDACTED


def test_nested_containers_under_secret_keys_are_redacted() -> None:
    out = scrub_extra({"password": [_SECRET, {"a": (_SECRET,)}], "ok": {"token": [_SECRET]}})
    assert _SECRET not in _dump(out)


def test_bytes_value_under_secret_key_is_redacted() -> None:
    assert scrub_extra({"token": _SECRET.encode()})["token"] == REDACTED


def test_bytearray_value_under_secret_key_is_redacted() -> None:
    assert scrub_extra({"token": bytearray(_SECRET.encode())})["token"] == REDACTED


def test_secret_in_a_set_or_frozenset_under_secret_key_is_redacted() -> None:
    assert _SECRET not in _dump(scrub_extra({"passwords": {_SECRET}}))
    assert _SECRET not in _dump(scrub_extra({"passwords": frozenset({_SECRET})}))


def test_secret_in_a_nested_set_under_a_benign_key_with_url_is_masked() -> None:
    out = scrub_extra({"items": {"socks5://user:pw@host:1080"}})
    assert "pw" not in _dump(out)


def test_object_with_secret_attributes_is_not_expanded_by_str() -> None:
    class Holder:
        def __repr__(self) -> str:
            return f"Holder(password={_SECRET})"

    out = scrub_extra({"password": Holder()})
    assert _SECRET not in _dump(out)


@pytest.mark.parametrize(
    "text",
    [
        "socks5://user:pw@host:1080",
        "socks5h://user:pw@host:1080",
        "http://user:pw@host",
        "https://user:p%40ss@host:8080/path?x=1",
        "failed to reach http://user:p@ss@host:8080 timeout",
        "a http://u:pw@h b https://u2:pw2@h2 c",
        "HTTP://USER:PW@HOST",
        "ftp+ssh://u:pw@h",
    ],
)
def test_url_userinfo_is_masked_in_free_text(text: str) -> None:
    out = scrub_extra({"error": text})["error"]
    assert "pw" not in str(out)
    assert "p@ss" not in str(out)


@pytest.mark.parametrize("text", ["user:pw@host:1080", "//user:pw@host", "user:pw@1.2.3.4:80"])
def test_schemeless_credentials_are_masked(text: str) -> None:
    assert "pw" not in str(scrub_extra({"proxy": text})["proxy"])


def test_unescaped_slash_in_password_is_masked() -> None:
    assert "se/cret" not in str(scrub_extra({"u": "http://user:se/cret@host"})["u"])


def test_dict_with_non_string_keys_and_depth_limit_do_not_raise() -> None:
    deep: dict[object, object] = {1: {2: {3: {4: {5: {6: {7: _SECRET}}}}}}}
    out = scrub_extra({"deep": deep, "t": {1: "x", None: "y"}})  # type: ignore[dict-item]
    assert _SECRET not in _dump(out)


def test_cyclic_structures_terminate_and_do_not_raise() -> None:
    cyc: dict[str, object] = {}
    cyc["self"] = cyc
    lst: list[object] = []
    lst.append(lst)
    lst.append(lst)
    start = time.perf_counter()
    scrub_extra({"cyc": cyc, "lst": lst})
    assert time.perf_counter() - start < 2


def test_wide_cyclic_list_does_not_blow_up_exponentially() -> None:
    lst: list[object] = []
    lst.extend([lst] * 12)
    start = time.perf_counter()
    scrub_extra({"lst": lst})
    assert time.perf_counter() - start < 2


def test_big_payload_is_fast() -> None:
    payload = {f"k{i}": f"value {i} http://u:p@h{i}" for i in range(20_000)}
    start = time.perf_counter()
    out = scrub_extra(payload)
    assert time.perf_counter() - start < 3
    assert "u:p@" not in _dump(out)


def test_regex_does_not_go_quadratic_on_hostile_text() -> None:
    text = ("a" * 500 + "://" + "b" * 500) * 400
    start = time.perf_counter()
    scrub_extra({"x": text})
    assert time.perf_counter() - start < 3


def test_the_callers_dict_is_not_mutated() -> None:
    original = {"password": _SECRET, "nested": {"token": [_SECRET]}, "u": "http://a:b@c"}
    snapshot = copy.deepcopy(original)
    scrub_extra(original)
    assert original == snapshot


# --------------------------------------------------------------------- log_event


class _Boom:
    def __str__(self) -> str:
        msg = "no str for you"
        raise RuntimeError(msg)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_journal")
async def test_all_tiers_see_only_the_scrubbed_value() -> None:
    seen: list[object] = []
    async with subscribe() as queue:
        await log_event(
            "ERROR",
            "adversarial_probe",
            extra={"password": _SECRET, "proxy": "socks5://u:pw-leak@h:1", "flag": True},
        )
        seen.append(queue.get_nowait())
    rows = await list_filtered_logs(LogFilter(limit=50))
    assert _SECRET not in _dump([row.model_dump() for row in rows])
    assert "pw-leak" not in _dump([row.model_dump() for row in rows])
    assert "pw-leak" not in repr(seen)
    assert _SECRET not in repr(seen)
    assert rows[0].extra["flag"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("_journal")
async def test_a_scrubber_failure_does_not_break_log_event() -> None:
    await log_event("INFO", "adversarial_boom", extra={_Boom(): "x"})  # ty: ignore[invalid-argument-type]
