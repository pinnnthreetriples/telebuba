"""Story design validation: entities in UTF-16, URL schemes, sticker geometry."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from schemas.scheduled_posts import ScheduledStoryOptions
from schemas.story_design import (
    StoryDesign,
    StoryEntity,
    StoryLinkSticker,
    normalise_story_url,
    period_hours_for,
    utf16_length,
)
from schemas.telegram_actions import PostStory


def _sticker(**fields: Any) -> dict[str, Any]:
    return {"url": "example.com", "label": "Go", "x": 10, "y": 10, **fields}


def _entity(offset: int, length: int) -> dict[str, Any]:
    return {"type": "bold", "offset": offset, "length": length}


def test_defaults_are_a_plain_24_hour_contacts_story() -> None:
    design = StoryDesign()

    assert (design.caption, design.entities, design.link_stickers) == (None, [], [])
    assert (design.period_hours, design.pinned, design.protect_content) == (24, False, False)
    assert design.privacy_preset == "contacts"
    assert design.period_seconds == 86_400


@pytest.mark.parametrize("hours", [6, 12, 24, 48])
def test_the_offered_periods(hours: int) -> None:
    assert StoryDesign.model_validate({"period_hours": hours}).period_seconds == hours * 3600


@pytest.mark.parametrize("hours", [0, 1, 7, 36, 72])
def test_other_periods_are_refused(hours: int) -> None:
    with pytest.raises(ValidationError):
        StoryDesign.model_validate({"period_hours": hours})


@pytest.mark.parametrize(
    ("seconds", "hours"),
    [(21_600, 6), (43_200, 12), (86_400, 24), (172_800, 48), (100_000, 24), (30_000, 6)],
)
def test_a_legacy_period_maps_to_the_nearest_offer(seconds: int, hours: int) -> None:
    assert period_hours_for(seconds) == hours


def test_utf16_length_counts_an_emoji_as_two() -> None:
    assert utf16_length("a\U0001f393b") == 4


def test_entities_are_checked_against_the_caption_in_utf16_units() -> None:
    caption = "\U0001f393 ab"  # 2 + 1 + 2 = 5 units, 4 characters

    assert StoryDesign.model_validate({"caption": caption, "entities": [_entity(3, 2)]}).entities
    with pytest.raises(ValidationError, match="past the end"):
        StoryDesign.model_validate({"caption": caption, "entities": [_entity(3, 3)]})


def test_nested_and_overlapping_entities_are_allowed() -> None:
    entities = [
        _entity(0, 5),
        {"type": "italic", "offset": 2, "length": 3},
        {"type": "spoiler", "offset": 4, "length": 1},
    ]

    assert len(StoryDesign.model_validate({"caption": "hello", "entities": entities}).entities) == 3


def test_entities_need_a_caption() -> None:
    with pytest.raises(ValidationError):
        StoryDesign.model_validate({"entities": [_entity(0, 1)]})


def test_at_most_a_hundred_entities_and_three_stickers() -> None:
    with pytest.raises(ValidationError):
        StoryDesign.model_validate({"caption": "x", "entities": [_entity(0, 1)] * 101})
    with pytest.raises(ValidationError):
        StoryDesign.model_validate({"link_stickers": [_sticker()] * 4})
    assert len(StoryDesign.model_validate({"link_stickers": [_sticker()] * 3}).link_stickers) == 3


def test_a_caption_is_capped_at_2048() -> None:
    assert StoryDesign(caption="x" * 2048)
    with pytest.raises(ValidationError):
        StoryDesign(caption="x" * 2049)


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "bold", "offset": -1, "length": 1},
        {"type": "bold", "offset": 0, "length": 0},
        {"type": "mention", "offset": 0, "length": 1},
        {"type": "bold", "offset": 0, "length": 1, "url": "https://a.co"},
        {"type": "text_url", "offset": 0, "length": 1},
        {"type": "text_url", "offset": 0, "length": 1, "url": "ftp://a.co/x"},
        {"type": "text_url", "offset": 0, "length": 1, "url": "javascript:alert(1)"},
    ],
)
def test_malformed_entities_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StoryEntity.model_validate(bad)


def test_a_text_url_entity_is_normalised() -> None:
    entity = StoryEntity(type="text_url", offset=0, length=3, url="example.com/x")

    assert entity.url == "https://example.com/x"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("example.com", "https://example.com"),
        ("  example.com/a?b=1 ", "https://example.com/a?b=1"),
        ("http://example.com", "http://example.com"),
        ("HTTPS://Example.com", "HTTPS://Example.com"),
        ("tg://resolve?domain=durov", "tg://resolve?domain=durov"),
        ("tg:resolve?domain=durov", "tg:resolve?domain=durov"),
        ("localhost:3000/x", "https://localhost:3000/x"),
        ("example.com:8080", "https://example.com:8080"),
    ],
)
def test_urls_get_https_when_they_have_no_scheme(raw: str, expected: str) -> None:
    assert normalise_story_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "  JaVaScRiPt:alert(1)",
        "data:text/html,<b>x</b>",
        "tg:",
        "tg://",
        "https://",
        "http://[::1",
        "http://a.co:99999",
    ],
)
def test_other_schemes_and_hostless_urls_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - the message differs per refusal
        normalise_story_url(raw)


def test_a_label_is_one_line() -> None:
    sticker = StoryLinkSticker.model_validate(_sticker(label="Go\n  to\tsite"))

    assert sticker.label == "Go to site"


def test_a_sticker_has_defaults_and_a_trimmed_label() -> None:
    sticker = StoryLinkSticker.model_validate(_sticker(label="  Go  "))

    assert (sticker.label, sticker.style, sticker.scale, sticker.rotation) == ("Go", "white", 1, 0)
    assert sticker.url == "https://example.com"


@pytest.mark.parametrize(
    "bad",
    [
        {"label": ""},
        {"label": "   "},
        {"label": "x" * 41},
        {"style": "neon"},
        {"x": -1},
        {"x": 100.5},
        {"y": 101},
        {"scale": 0.4},
        {"scale": 2.6},
        {"rotation": -180},
        {"rotation": 180.1},
        {"url": ""},
    ],
)
def test_sticker_bounds(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        StoryLinkSticker.model_validate(_sticker(**bad))


@pytest.mark.parametrize("rotation", [-179.9, 0, 45, 180])
def test_sticker_rotation_is_in_the_half_open_range(rotation: float) -> None:
    assert StoryLinkSticker.model_validate(_sticker(rotation=rotation)).rotation == rotation


def test_the_post_action_checks_its_entities_against_the_caption() -> None:
    base: dict[str, Any] = {"filename": "s.jpg", "content": b"x", "media_kind": "image"}

    PostStory.model_validate({**base, "caption": "abc", "entities": [_entity(0, 3)]})
    with pytest.raises(ValidationError):
        PostStory.model_validate({**base, "caption": "abc", "entities": [_entity(0, 4)]})


def test_scheduled_options_round_trip_a_design() -> None:
    design = StoryDesign.model_validate(
        {
            "caption": "hello",
            "entities": [_entity(0, 5)],
            "period_hours": 12,
            "pinned": True,
            "link_stickers": [_sticker()],
        }
    )

    options = ScheduledStoryOptions.from_design(design, media_kind="image", collage_layout=None)
    restored = ScheduledStoryOptions.model_validate_json(options.model_dump_json()).to_design()

    assert restored == design
