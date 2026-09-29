"""``exception_text`` masks a key even when h11 has escaped it inside the message."""

from __future__ import annotations

import httpx
import pytest

from core._llm_redact import exception_text


def test_a_key_with_a_control_character_is_masked_in_its_escaped_form() -> None:
    key = "sk-abcd1234\nwxyz5678"
    exc = httpx.LocalProtocolError(f"Illegal header value {f'Bearer {key}'.encode()!r}")
    text = exception_text(exc, key)
    assert text.startswith("LocalProtocolError: Illegal header value")
    assert "sk-abcd1234" not in text
    assert "wxyz5678" not in text


# h11 quotes the header as a bytes repr (the same ``{!r}`` format as above), which doubles
# a backslash and, when both quote kinds occur, escapes the single quote — so a run that
# spans either character is not in the message verbatim.
@pytest.mark.parametrize(
    ("key", "pieces"),
    [
        ("FAKE\\KEYabcdefgh1234\n", ["FAKE", "KEYabcdefgh1234"]),
        ("FAKEKEY'ab\"cd1234\n", ["FAKEKEY", 'ab"cd1234']),
    ],
)
def test_a_key_run_escaped_by_the_bytes_repr_is_masked(key: str, pieces: list[str]) -> None:
    exc = httpx.LocalProtocolError(f"Illegal header value {f'Bearer {key}'.encode()!r}")
    text = exception_text(exc, key)
    assert text.startswith("LocalProtocolError: Illegal header value")
    for piece in pieces:
        assert piece not in text


def test_a_message_without_the_key_is_unchanged() -> None:
    assert exception_text(httpx.ConnectError("boom"), "sk-abcd1234") == "ConnectError: boom"


def test_a_run_that_is_a_prefix_of_a_later_run_does_not_leave_its_tail_in_clear() -> None:
    key = "sk-abcd1234\nsk-abcd1234-SECRETTAIL"
    exc = httpx.LocalProtocolError(f"Illegal header value {f'Bearer {key}'.encode()!r}")
    text = exception_text(exc, key)
    assert "SECRETTAIL" not in text
    assert "sk-abcd1234" not in text
