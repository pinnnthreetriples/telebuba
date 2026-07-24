"""Business boundaries for deciding whether a channel post is commentable."""

from __future__ import annotations

import pytest

from core.config import settings
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import _filters


def _post(text: str = "", *, media: bool = False, forwarded: bool = False) -> NewPostEvent:
    return NewPostEvent(
        channel="@channel",
        post_id=1,
        text=text,
        has_media=media,
        is_forward=forwarded,
    )


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (_post("ordinary caption", forwarded=True), "forward"),
        (_post("   ", media=True), "media_no_caption"),
        (_post("\n\t"), "empty"),
        (_post("https://example.com"), "link_only"),
        (_post("https://example.com ok"), "link_only"),
        (_post("caption", media=True), None),
        (_post("обычный текст без ссылки"), None),
    ],
)
def test_post_filter_matrix(event: NewPostEvent, reason: str | None) -> None:
    assert _filters.filter_reason(event) == reason


def test_forward_precedes_other_rejection_reasons() -> None:
    assert _filters.filter_reason(_post("", media=True, forwarded=True)) == "forward"


def test_link_only_character_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.neurocomment, "link_only_max_word_chars", 3)

    assert _filters.filter_reason(_post("https://example.com abc")) == "link_only"
    assert _filters.filter_reason(_post("https://example.com abcd")) is None


@pytest.mark.parametrize(
    "text",
    [
        "Details: https://example.com launch tomorrow",
        "Смотрите https://example.com — подробности внутри канала",
        "Фото дня https://example.com прекрасное настроение",
    ],
)
def test_link_inside_substantive_multilingual_text_is_allowed(text: str) -> None:
    assert _filters.filter_reason(_post(text)) is None
