"""Channel-token parsing at its shared home in ``core``.

These cases previously lived against ``services.warming.channels``; they moved
with the functions so the two callers (warming's paste box, neurocomment
discovery) share one verified normalizer instead of two drifting copies.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.channel_tokens import (
    channel_fold_sql,
    dedup_key,
    extract_invite_hash,
    normalize_channel,
    parse_channels,
    parse_message_link,
)

# Telegram's own username ceiling — what discovery passes.
HANDLE_MAX = 32
# The wider bound warming passes (settings.warming.max_channel_length default).
WARMING_MAX = 120


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+AbCdEfGhIjK", "AbCdEfGhIjK"),
        ("https://t.me/+AbCdEfGhIjK", "AbCdEfGhIjK"),
        ("http://t.me/+AbCdEfGhIjK", "AbCdEfGhIjK"),
        ("t.me/joinchat/AbCdEfGhIjK", "AbCdEfGhIjK"),
        ("telegram.me/+AbCdEfGhIjK", "AbCdEfGhIjK"),
        ("https://t.me/+AbCdEfGhIjK?foo=1", "AbCdEfGhIjK"),
        ("<https://t.me/+AbCdEfGhIjK>", "AbCdEfGhIjK"),
        # too short for the hash pattern
        ("+short", None),
        # a bare hash without a prefix must not shadow a username
        ("AbCdEfGhIjK", None),
        ("@durov", None),
    ],
)
def test_extract_invite_hash(raw: str, expected: str | None) -> None:
    assert extract_invite_hash(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("durov", "durov"),
        ("@durov", "durov"),
        ("  @durov  ", "durov"),
        ("<@durov>", "durov"),
        ("@durov/", "durov"),
        ("https://t.me/durov", "durov"),
        ("http://t.me/durov", "durov"),
        ("t.me/durov", "durov"),
        ("telegram.me/durov", "durov"),
        ("HTTPS://T.ME/durov", "durov"),
        # a public post link reduces to its channel
        ("t.me/durov/123", "durov"),
        ("https://t.me/durov/123?single", "durov"),
        # query strings are dropped before validation
        ("t.me/durov?start=1", "durov"),
        # case is preserved — Telegram handles are case-insensitive but we keep
        # whatever the operator/Telegram gave us and fold only for dedup
        ("@DuroV", "DuroV"),
    ],
)
def test_normalize_channel_accepts(raw: str, expected: str) -> None:
    assert normalize_channel(raw, max_length=HANDLE_MAX) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "@",
        # private chat links carry no resolvable handle
        "t.me/c/1234567890/5",
        "https://t.me/c/1234567890",
        # a bare token with a slash is not a link we recognise
        "durov/123",
        # below the 3-character minimum
        "ab",
        # illegal characters for a handle
        "@dur-ov",
        "@дуров",
        "t.me/dur ov",
    ],
)
def test_normalize_channel_rejects(raw: str) -> None:
    assert normalize_channel(raw, max_length=HANDLE_MAX) is None


def test_invite_link_survives_normalization_as_plus_key() -> None:
    assert normalize_channel("https://t.me/+AbCdEfGhIjK", max_length=HANDLE_MAX) == "+AbCdEfGhIjK"


def test_max_length_bound_is_per_caller() -> None:
    """The bound is an argument, so each caller owns its own policy."""
    # 33 chars: over Telegram's handle ceiling, under warming's wider bound.
    long_handle = "a" * 33
    assert normalize_channel(long_handle, max_length=HANDLE_MAX) is None
    # Still rejected at the wider bound, because the token regex caps at 32 too —
    # the length bound is the *outer* guard, not a way to widen the pattern.
    assert normalize_channel(long_handle, max_length=WARMING_MAX) is None
    # A 32-char handle passes both.
    assert normalize_channel("a" * 32, max_length=HANDLE_MAX) == "a" * 32


def test_max_length_can_reject_before_the_pattern() -> None:
    """A handle inside the regex but over a tighter bound is dropped."""
    assert normalize_channel("abcdef", max_length=3) is None
    assert normalize_channel("abc", max_length=3) == "abc"


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        # public usernames are case-insensitive
        ("@Alpha", "@alpha", True),
        ("Alpha", "ALPHA", True),
        # invite hashes are case-sensitive: two different invites must not merge
        ("+AbC", "+abc", False),
    ],
)
def test_dedup_key_case_rules(left: str, right: str, *, same: bool) -> None:
    left_key = dedup_key(left.lstrip("@"))
    right_key = dedup_key(right.lstrip("@"))
    assert (left_key == right_key) is same


def test_dedup_key_ignores_the_at_sigil() -> None:
    """``@News`` and ``news`` are one channel — one peer id — so they must fold together.

    ``normalize_channel`` already strips the ``@``, so this only matters for handles
    that reach ``dedup_key`` unnormalized: the campaign-link box and legacy rows.
    """
    assert dedup_key("@News") == dedup_key("news") == "news"
    # A ``+HASH`` invite key is never touched: '@' is not legal in one anyway.
    assert dedup_key("+AbCdEfGh") == "+AbCdEfGh"


@pytest.mark.parametrize("handle", ["@News", "news", "NEWS", "+AbCdEfGh", "+abcdefgh"])
def test_channel_fold_sql_mirrors_dedup_key(handle: str) -> None:
    """The SQL fold and ``dedup_key`` must agree, or the index and the reads disagree."""
    connection = sqlite3.connect(":memory:")
    try:
        # The expression names its operand three times, hence the repeated binding.
        folded = connection.execute(
            f"SELECT {channel_fold_sql('?')}",
            (handle,) * 3,
        ).fetchone()[0]
    finally:
        connection.close()
    assert folded == dedup_key(handle)


def test_parse_channels_dedups_preserving_order() -> None:
    raw = "@alpha, https://t.me/beta\n@ALPHA t.me/gamma/12 beta"
    assert parse_channels(raw, max_length=HANDLE_MAX) == ["alpha", "beta", "gamma"]


def test_parse_channels_skips_junk_without_failing() -> None:
    raw = "@ok1 ab @дуров t.me/c/999/1 @ok2"
    assert parse_channels(raw, max_length=HANDLE_MAX) == ["ok1", "ok2"]


def test_parse_channels_empty_input() -> None:
    assert parse_channels("", max_length=HANDLE_MAX) == []
    assert parse_channels("   \n  ", max_length=HANDLE_MAX) == []


def test_parse_channels_keeps_distinct_invite_hashes() -> None:
    raw = "https://t.me/+AbCdEfGhIjK https://t.me/+abcdefghijk"
    assert parse_channels(raw, max_length=HANDLE_MAX) == ["+AbCdEfGhIjK", "+abcdefghijk"]


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://t.me/durov/42", ("durov", 42)),
        ("t.me/durov/42", ("durov", 42)),
        ("https://t.me/@durov/42/", ("durov", 42)),
        ("https://t.me/durov/42?single", ("durov", 42)),
        # The private form: ``<internal>`` is the chat's RAW POSITIVE id, the same
        # unmarked convention the chat actions pin, so it rides back as digits.
        ("https://t.me/c/1234567890/42", ("1234567890", 42)),
        # A forum thread names its message LAST, the topic in between.
        ("https://t.me/c/1234567890/7/42", ("1234567890", 42)),
    ],
)
def test_parse_message_link_reads_both_public_and_private_forms(
    link: str,
    expected: tuple[str, int],
) -> None:
    assert parse_message_link(link) == expected


@pytest.mark.parametrize(
    "link",
    [
        # A channel link with no message on it — the whole point is the message id.
        "https://t.me/durov",
        "https://t.me/c/1234567890",
        "https://t.me/durov/abc",
        "https://t.me/c/notanid/42",
        "https://t.me/durov/0",
        # Not a t.me link at all.
        "https://example.com/durov/42",
        "durov/42",
        "",
    ],
)
def test_parse_message_link_refuses_anything_that_is_not_one(link: str) -> None:
    assert parse_message_link(link) is None
