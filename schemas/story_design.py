"""A story's design: caption with formatting, link stickers, lifetime, pin and audience.

One model shared by the immediate post, the scheduled post, single and bulk. Which
parts need Telegram Premium is NOT decided here: ``services.accounts._story_premium``
owns that classification. The numeric ceilings below are Telegram's own; the tunable
limits (free caption length, stickers per story, label length) live in
``core.config`` and are enforced by the service, because ``schemas`` may not read
settings.
"""

from __future__ import annotations

import re
from typing import Final, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

STORY_CAPTION_MAX: Final = 2048
STORY_ENTITIES_MAX: Final = 100
STORY_STICKERS_MAX: Final = 3
STORY_LABEL_MAX: Final = 40
STORY_URL_MAX: Final = 2048
_URL_SCHEMES: Final = frozenset({"http", "https", "tg"})
_WEB_SCHEMES: Final = frozenset({"http", "https"})
_DEFAULT_SCHEME: Final = "https"
# ``scheme:`` at the start, but not ``host:8080`` (a port is digits up to a path or the end).
_SCHEME_RE: Final = re.compile(r"^([a-z][a-z0-9+.\-]*):(?!\d+(?:[/?#]|$))", re.IGNORECASE)

StoryPrivacyPreset = Literal["contacts", "close_friends", "public"]
StoryEntityType = Literal["bold", "italic", "underline", "strike", "spoiler", "code", "text_url"]
StoryStickerStyle = Literal["white", "dark", "glass", "accent"]
StoryPeriodHours = Literal[6, 12, 24, 48]
# What a Premium story feature is called when a free account loses it (one vocabulary
# for the post result, the scheduled row and the SPA's notice).
PremiumFeature = Literal["period", "link_stickers", "long_caption", "formatting"]
_PERIOD_CHOICES: Final[tuple[StoryPeriodHours, ...]] = (6, 12, 24, 48)


def utf16_length(text: str) -> int:
    """Length in UTF-16 code units, the unit Telegram counts entity offsets in."""
    return len(text.encode("utf-16-le")) // 2


def period_hours_for(seconds: int) -> StoryPeriodHours:
    """The offered lifetime closest to ``seconds`` (a legacy request may ask for any)."""
    return min(_PERIOD_CHOICES, key=lambda hours: abs(hours * 3600 - seconds))


def normalise_story_url(value: str) -> str:
    """The URL with ``https://`` added when it has no scheme; refuse any other scheme."""
    url = value.strip()
    explicit = _SCHEME_RE.match(url)
    if explicit is None:
        url = f"{_DEFAULT_SCHEME}://{url}"
    scheme = explicit.group(1).lower() if explicit else _DEFAULT_SCHEME
    if scheme not in _URL_SCHEMES:
        msg = "url scheme must be http, https or tg"
        raise ValueError(msg)
    try:
        parts = urlsplit(url)
        host = parts.hostname
        _ = parts.port
    except ValueError as exc:
        msg = "url is malformed"
        raise ValueError(msg) from exc
    if scheme in _WEB_SCHEMES and not host:
        msg = "url has no host"
        raise ValueError(msg)
    if scheme == "tg" and not url[len("tg:") :].lstrip("/"):
        msg = "tg url has no resource"
        raise ValueError(msg)
    return url


class StoryEntity(BaseModel):
    """One formatted span of the caption; ``offset``/``length`` are UTF-16 units."""

    type: StoryEntityType
    offset: int = Field(ge=0)
    length: int = Field(ge=1)
    url: str | None = Field(default=None, max_length=STORY_URL_MAX)

    @model_validator(mode="after")
    def _check_url(self) -> StoryEntity:
        if self.type == "text_url":
            if not self.url:
                msg = "a text_url entity needs a url"
                raise ValueError(msg)
            self.url = normalise_story_url(self.url)
        elif self.url is not None:
            msg = "only a text_url entity carries a url"
            raise ValueError(msg)
        return self


def check_entities_in_caption(caption: str | None, entities: list[StoryEntity]) -> None:
    """Raise ``ValueError`` when an entity reaches outside the caption (nesting is fine)."""
    if not entities:
        return
    size = utf16_length(caption or "")
    if any(entity.offset + entity.length > size for entity in entities):
        msg = "an entity reaches past the end of the caption"
        raise ValueError(msg)


class StoryLinkSticker(BaseModel):
    """A tappable link plate; ``x``/``y`` are its centre in percent of the media."""

    url: str = Field(min_length=1, max_length=STORY_URL_MAX)
    label: str = Field(min_length=1, max_length=STORY_LABEL_MAX)
    style: StoryStickerStyle = "white"
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    scale: float = Field(default=1.0, ge=0.5, le=2.5)
    # Degrees, clockwise (Telegram's convention), in (-180, 180].
    rotation: float = Field(default=0.0, gt=-180, le=180)

    @field_validator("url")
    @classmethod
    def _normalise_url(cls, value: str) -> str:
        return normalise_story_url(value)

    @field_validator("label")
    @classmethod
    def _strip_label(cls, value: str) -> str:
        # One line: Pillow cannot anchor multi-line text, so a newline or tab is a space.
        stripped = " ".join(value.split())
        if not stripped:
            msg = "label is blank"
            raise ValueError(msg)
        return stripped


class StoryDesign(BaseModel):
    caption: str | None = Field(default=None, max_length=STORY_CAPTION_MAX)
    entities: list[StoryEntity] = Field(default_factory=list, max_length=STORY_ENTITIES_MAX)
    period_hours: StoryPeriodHours = 24
    pinned: bool = False
    protect_content: bool = False
    privacy_preset: StoryPrivacyPreset = "contacts"
    link_stickers: list[StoryLinkSticker] = Field(
        default_factory=list, max_length=STORY_STICKERS_MAX
    )

    @model_validator(mode="after")
    def _check_entities(self) -> StoryDesign:
        check_entities_in_caption(self.caption, self.entities)
        return self

    @property
    def period_seconds(self) -> int:
        return self.period_hours * 3600
