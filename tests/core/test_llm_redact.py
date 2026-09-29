"""``exception_text`` masks a key even when h11 has escaped it inside the message."""

from __future__ import annotations

import httpx

from core._llm_redact import exception_text


def test_a_key_with_a_control_character_is_masked_in_its_escaped_form() -> None:
    key = "sk-abcd1234\nwxyz5678"
    exc = httpx.LocalProtocolError(f"Illegal header value {f'Bearer {key}'.encode()!r}")
    text = exception_text(exc, key)
    assert text.startswith("LocalProtocolError: Illegal header value")
    assert "sk-abcd1234" not in text
    assert "wxyz5678" not in text


def test_a_message_without_the_key_is_unchanged() -> None:
    assert exception_text(httpx.ConnectError("boom"), "sk-abcd1234") == "ConnectError: boom"
