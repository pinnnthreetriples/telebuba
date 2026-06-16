"""Small shared helpers for the Telegram gateway submodules."""

from __future__ import annotations


def optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
