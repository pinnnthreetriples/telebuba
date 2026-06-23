"""Tests for the neurocomment page's pure-display label maps (issue #119).

The page itself is ``pragma: no cover`` (UI-thin, exercised manually); these two
translation helpers carry the only branchy logic, so they get a unit test.
"""

from __future__ import annotations

import pytest

from features.neurocomment import register_neurocomment_page
from features.neurocomment._page import channel_status_label, health_label


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ready", "Готов"),
        ("comments_off", "Комментарии выключены"),
        ("join_by_request", "Вступление по заявке"),
        ("captcha_gated", "Капча / блок записи"),
        ("throttled", "Лимит исчерпан"),
    ],
)
def test_channel_status_label_known(status: str, expected: str) -> None:
    assert channel_status_label(status) == expected


def test_channel_status_label_unknown_falls_back() -> None:
    assert channel_status_label("weird") == "weird"


@pytest.mark.parametrize(("health", "expected"), [("ready", "Готов"), ("blocked", "Заблокирован")])
def test_health_label_known(health: str, expected: str) -> None:
    assert health_label(health) == expected


def test_health_label_unknown_falls_back() -> None:
    assert health_label("nope") == "nope"


def test_page_registration_is_importable() -> None:
    # Smoke test: registering the page builds the route without raising (mirrors
    # how the warming feature is smoke-covered).
    register_neurocomment_page()
