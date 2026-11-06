"""Story image composition and write-action tests."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image, ImageFont
from telethon.tl.functions.stories import (
    CanSendStoryRequest,
    DeleteStoriesRequest,
    SendStoryRequest,
    TogglePinnedRequest,
)
from telethon.tl.types import InputPeerUser, MediaAreaUrl, MessageEntityTextUrl

from core.telegram_client import _media_stories, execute
from schemas.telegram_actions import PostStory, RemoveStory, ToggleStoryPinned
from schemas.telegram_actions_media import StoryLink, StoryMention
from tests.core.telegram_client.helpers import patch_action_client as _patch_client


def _jpeg(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, colour).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_execute_post_story_dispatches_story_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    uploaded_bytes: list[bytes] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, file: BytesIO, *, file_name: str) -> object:
            assert file_name == "story.jpg"
            uploaded_bytes.append(file.read())
            return MagicMock()

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, SendStoryRequest):
                return SimpleNamespace(
                    updates=[
                        SimpleNamespace(),
                        SimpleNamespace(story=SimpleNamespace(id=777)),
                    ],
                )
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-story",
        PostStory(
            filename="story.jpg",
            content=_jpeg((400, 500), (255, 0, 0)),
            media_kind="image",
            caption="hi",
            privacy_preset="contacts",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 777
    assert any(isinstance(req, CanSendStoryRequest) for req in captured)
    assert any(isinstance(req, SendStoryRequest) for req in captured)
    with Image.open(BytesIO(uploaded_bytes[0])) as sent:
        assert sent.size == (1080, 1920)


def test_normalize_story_image_renders_blurred_background_canvas() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        _normalize_story_image_for_telegram,
    )

    source_rgb = (200, 50, 30)
    out = _normalize_story_image_for_telegram(_jpeg((800, 600), source_rgb))

    with Image.open(BytesIO(out)) as result:
        assert result.size == (1080, 1920)
        assert result.mode == "RGB"
        assert result.format == "JPEG"
        corner = result.convert("RGB").getpixel((10, 10))
        assert isinstance(corner, tuple)
        assert corner != (0, 0, 0)
        centre = result.convert("RGB").getpixel((540, 960))
        assert isinstance(centre, tuple)
        assert abs(int(centre[0]) - source_rgb[0]) < 30


def test_normalize_story_image_rejects_non_image_bytes_with_stable_code() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryImageNormalisationError,
        _normalize_story_image_for_telegram,
    )

    with pytest.raises(StoryImageNormalisationError) as excinfo:
        _normalize_story_image_for_telegram(b"not an image")
    assert str(excinfo.value) == "story_image_invalid"
    assert not any("Ѐ" <= ch <= "ӿ" for ch in str(excinfo.value))
    assert "magic=" in str(excinfo.value.__cause__)


def test_normalize_story_image_rejects_truncated_file_with_stable_code() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryImageNormalisationError,
        _normalize_story_image_for_telegram,
    )

    truncated = _jpeg((200, 200), (0, 0, 0))[:400]
    with pytest.raises(StoryImageNormalisationError) as excinfo:
        _normalize_story_image_for_telegram(truncated)
    assert str(excinfo.value) == "story_image_invalid"


def test_normalize_story_image_rejects_decompression_bomb_with_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryImageNormalisationError,
        _normalize_story_image_for_telegram,
    )

    buffer = BytesIO()
    Image.new("RGB", (100, 100)).save(buffer, format="PNG")
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(StoryImageNormalisationError) as excinfo:
        _normalize_story_image_for_telegram(buffer.getvalue())
    assert str(excinfo.value) == "story_image_invalid"


@pytest.mark.parametrize(
    ("count", "layout"),
    [(2, "v2"), (4, "grid2x2"), (6, "grid2x3")],
)
def test_compose_story_collage_produces_canvas_jpeg(count: int, layout: str) -> None:
    from core.telegram_client._story_image import _compose_story_collage  # noqa: PLC0415

    images = [_jpeg((400, 500), (10 * i, 20, 30)) for i in range(count)]
    out = _compose_story_collage(images, layout)

    with Image.open(BytesIO(out)) as result:
        assert result.size == (1080, 1920)
        assert result.format == "JPEG"


def test_compose_story_collage_default_layout_is_first_for_count() -> None:
    from core.telegram_client._story_image import _default_collage_layout  # noqa: PLC0415

    assert _default_collage_layout(2) == "v2"
    assert _default_collage_layout(3) == "v3"


def test_compose_story_collage_unknown_layout_raises() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryCollageLayoutError,
        _compose_story_collage,
    )

    images = [_jpeg((100, 100), (0, 0, 0)), _jpeg((100, 100), (255, 255, 255))]
    with pytest.raises(StoryCollageLayoutError) as excinfo:
        _compose_story_collage(images, "grid2x2")
    assert str(excinfo.value) == "story_collage_unknown_layout"
    assert "unknown collage layout" in str(excinfo.value.__cause__)


def test_compose_story_collage_unsupported_count_raises() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryCollageLayoutError,
        _compose_story_collage,
    )

    with pytest.raises(StoryCollageLayoutError) as excinfo:
        _compose_story_collage([_jpeg((100, 100), (0, 0, 0))] * 7, "v2")
    assert "unsupported collage image count" in str(excinfo.value.__cause__)


def test_compose_story_collage_rejects_undecodable_image() -> None:
    from core.telegram_client._story_image import (  # noqa: PLC0415
        StoryImageNormalisationError,
        _compose_story_collage,
    )

    with pytest.raises(StoryImageNormalisationError):
        _compose_story_collage([_jpeg((100, 100), (0, 0, 0)), b"not an image"], "v2")


@pytest.mark.asyncio
async def test_execute_post_story_collage_uploads_single_composite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    uploaded_bytes: list[bytes] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, file: BytesIO, *, file_name: str) -> object:  # noqa: ARG002
            uploaded_bytes.append(file.read())
            return MagicMock()

        async def __call__(self, request: object) -> object:
            captured.append(request)
            if isinstance(request, SendStoryRequest):
                return SimpleNamespace(updates=[SimpleNamespace(story=SimpleNamespace(id=555))])
            return MagicMock()

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-collage",
        PostStory(
            filename="story.jpg",
            content=_jpeg((400, 500), (200, 0, 0)),
            media_kind="image",
            extra_images=[_jpeg((400, 500), (0, 200, 0))],
            collage_layout="h2",
        ),
    )

    assert result.status == "ok"
    assert result.message_id == 555
    assert any(isinstance(req, SendStoryRequest) for req in captured)
    assert len(uploaded_bytes) == 1
    with Image.open(BytesIO(uploaded_bytes[0])) as sent:
        assert sent.size == (1080, 1920)


@pytest.mark.asyncio
async def test_execute_post_story_collage_unknown_layout_surfaces_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def connect(self) -> None:
            return None

        async def get_input_entity(self, entity: str) -> object:
            assert entity == "me"
            return MagicMock()

        async def upload_file(self, _file: BytesIO, *, file_name: str) -> object:  # noqa: ARG002
            msg = "upload must not run for an unresolved layout"
            raise AssertionError(msg)

        async def __call__(self, _request: object) -> object:
            return MagicMock(id=1)

    _patch_client(monkeypatch, FakeClient())

    result = await execute(
        "acc-collage-bad",
        PostStory(
            filename="story.jpg",
            content=_jpeg((400, 500), (200, 0, 0)),
            media_kind="image",
            extra_images=[_jpeg((400, 500), (0, 200, 0))],
            collage_layout="grid2x2",
        ),
    )

    assert result.status == "failed"
    assert result.error_message == "story_collage_unknown_layout"


@pytest.mark.asyncio
async def test_execute_remove_story_sends_delete_stories_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return [9876]

    _patch_client(monkeypatch, FakeClient())
    result = await execute("acc-story-rm", RemoveStory(story_id=9876))

    assert result.status == "ok"
    delete_requests = [req for req in captured if isinstance(req, DeleteStoriesRequest)]
    assert len(delete_requests) == 1
    assert delete_requests[0].id == [9876]


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [True, False])
async def test_execute_toggle_story_pinned_sends_toggle_request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pinned: bool,
) -> None:
    captured: list[object] = []

    class FakeClient:
        async def connect(self) -> None:
            return None

        async def __call__(self, request: object) -> object:
            captured.append(request)
            return [3210]

    _patch_client(monkeypatch, FakeClient())
    result = await execute("acc-story-pin", ToggleStoryPinned(story_id=3210, pinned=pinned))

    assert result.status == "ok"
    toggles = [req for req in captured if isinstance(req, TogglePinnedRequest)]
    assert len(toggles) == 1
    assert toggles[0].id == [3210]
    assert toggles[0].pinned is pinned


def _loose(value: list[dict[str, Any]]) -> Any:
    """Raw JSON-shaped values, as the API hands them over, for pydantic to validate."""
    return value


def _url(area: object) -> str:
    assert isinstance(area, MediaAreaUrl)
    return area.url


class _StoryClient:
    """Records the requests and the bytes uploaded; answers a story id."""

    def __init__(self) -> None:
        self.requests: list[object] = []
        self.uploads: list[tuple[str, bytes]] = []

    async def connect(self) -> None:
        return None

    async def get_input_entity(self, entity: object) -> object:
        return (
            MagicMock()
            if entity == "me"
            else InputPeerUser(user_id=int(str(entity)), access_hash=1)
        )

    async def upload_file(self, file: BytesIO, *, file_name: str) -> object:
        self.uploads.append((file_name, file.read()))
        return MagicMock()

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(request, SendStoryRequest):
            return SimpleNamespace(updates=[SimpleNamespace(story=SimpleNamespace(id=5))])
        return MagicMock()

    def sent(self) -> SendStoryRequest:
        return next(r for r in self.requests if isinstance(r, SendStoryRequest))


@pytest.fixture
def story_client(monkeypatch: pytest.MonkeyPatch) -> _StoryClient:
    client = _StoryClient()
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(
        "core.telegram_client._story_stickers._font",
        lambda _text, size: ImageFont.load_default(max(size, 1)),
    )
    return client


@pytest.mark.asyncio
async def test_caption_entities_map_to_telegram_entities_with_utf16_offsets(
    story_client: _StoryClient,
) -> None:
    # "Hi <emoji> " is 6 UTF-16 units (the emoji is two), so "world" starts at 6.
    result = await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((400, 500), (9, 9, 9)),
            media_kind="image",
            caption="Hi \U0001f393 world  ",
            entities=_loose(
                [
                    {"type": "bold", "offset": 6, "length": 5},
                    {"type": "italic", "offset": 0, "length": 2},
                    {"type": "underline", "offset": 0, "length": 2},
                    {"type": "strike", "offset": 3, "length": 2},
                    {"type": "spoiler", "offset": 6, "length": 2},
                    {"type": "code", "offset": 6, "length": 5},
                    {"type": "text_url", "offset": 6, "length": 7, "url": "example.com/a"},
                ]
            ),
            mentions=[StoryMention(user_id=11, username="alpha")],
        ),
    )

    assert result.status == "ok"
    sent = story_client.sent()
    assert sent.caption == "Hi \U0001f393 world @alpha"
    assert sent.entities is not None
    kinds = [(type(e).__name__, e.offset, e.length) for e in sent.entities]
    assert kinds == [
        ("MessageEntityBold", 6, 5),
        ("MessageEntityItalic", 0, 2),
        ("MessageEntityUnderline", 0, 2),
        ("MessageEntityStrike", 3, 2),
        ("MessageEntitySpoiler", 6, 2),
        ("MessageEntityCode", 6, 5),
        # The caption loses its trailing blanks, which cuts this one from 7 to 5.
        ("MessageEntityTextUrl", 6, 5),
        # Mentions come after the caption's own entities, so those offsets never move.
        ("InputMessageEntityMentionName", 12, 6),
    ]
    link = next(e for e in sent.entities if isinstance(e, MessageEntityTextUrl))
    assert link.url == "https://example.com/a"


@pytest.mark.asyncio
async def test_leading_blanks_are_trimmed_and_entities_shift_with_them(
    story_client: _StoryClient,
) -> None:
    # "\n  " is 3 units: "bold" at 3 becomes "bold" at 0; an entity on the blanks is dropped.
    await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((400, 500), (9, 9, 9)),
            media_kind="image",
            caption="\n  bold text",
            entities=_loose(
                [
                    {"type": "bold", "offset": 3, "length": 4},
                    {"type": "italic", "offset": 0, "length": 2},
                    {"type": "underline", "offset": 1, "length": 6},
                ]
            ),
        ),
    )

    sent = story_client.sent()
    assert sent.caption == "bold text"
    assert sent.entities is not None
    assert [(type(e).__name__, e.offset, e.length) for e in sent.entities] == [
        ("MessageEntityBold", 0, 4),
        ("MessageEntityUnderline", 0, 4),
    ]


@pytest.mark.asyncio
async def test_an_entity_trimmed_to_nothing_is_dropped(story_client: _StoryClient) -> None:
    await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((400, 500), (9, 9, 9)),
            media_kind="image",
            caption="Hi   ",
            entities=_loose([{"type": "bold", "offset": 3, "length": 2}]),
        ),
    )

    sent = story_client.sent()
    assert (sent.caption, sent.entities) == ("Hi", None)


@pytest.mark.asyncio
async def test_pinned_and_a_48_hour_period_reach_telegram(story_client: _StoryClient) -> None:
    await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((400, 500), (9, 9, 9)),
            media_kind="image",
            pinned=True,
            period_seconds=172_800,
            protect_content=True,
        ),
    )

    sent = story_client.sent()
    assert (sent.pinned, sent.period, sent.noforwards) == (True, 172_800, True)


@pytest.mark.asyncio
async def test_an_unpinned_story_leaves_the_pin_flag_off(story_client: _StoryClient) -> None:
    await execute(
        "acc",
        PostStory(filename="s.jpg", content=_jpeg((400, 500), (9, 9, 9)), media_kind="image"),
    )

    assert not story_client.sent().pinned


@pytest.mark.asyncio
async def test_photo_stickers_are_drawn_into_the_image_and_become_tap_areas(
    story_client: _StoryClient,
) -> None:
    await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((1080, 1920), (128, 128, 128)),
            media_kind="image",
            link_stickers=_loose(
                [
                    {"url": "example.com", "label": "Shop", "x": 30, "y": 25, "rotation": 15},
                    {
                        "url": "tg://resolve?domain=x",
                        "label": "Chat",
                        "x": 70,
                        "y": 75,
                        "style": "dark",
                    },
                ]
            ),
        ),
    )

    areas = story_client.sent().media_areas
    assert areas is not None
    assert [_url(a) for a in areas] == ["https://example.com", "tg://resolve?domain=x"]
    first = areas[0].coordinates
    assert (first.x, first.y, first.rotation) == pytest.approx((30, 25, 15))
    uploaded = story_client.uploads[0][1]
    with Image.open(BytesIO(uploaded)) as image:
        assert image.size == (1080, 1920)
        # The plate under the tap area is not the grey background any more.
        spot = (round(first.x / 100 * 1080), round(first.y / 100 * 1920))
        pixel = image.getpixel(spot)
        assert isinstance(pixel, tuple)
        assert max(abs(int(c) - 128) for c in pixel) > 40
        # And an untouched corner still is.
        assert image.getpixel((5, 5)) == pytest.approx((128, 128, 128), abs=4)


@pytest.mark.asyncio
async def test_the_masstagging_link_becomes_one_white_sticker_at_its_old_place(
    story_client: _StoryClient,
) -> None:
    await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((1080, 1920), (128, 128, 128)),
            media_kind="image",
            link=StoryLink(url="https://t.me/school", text="Go"),
        ),
    )

    areas = story_client.sent().media_areas
    assert areas is not None
    assert [_url(a) for a in areas] == ["https://t.me/school"]
    coords = areas[0].coordinates
    assert (coords.x, coords.y, coords.rotation) == (50, 82, 0)
    assert coords.h == pytest.approx(6, abs=0.1)


def test_a_link_text_no_font_can_draw_falls_back_to_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.telegram_client._media_stories.can_render_label", lambda text: text.isascii()
    )

    sticker = _media_stories._link_sticker(StoryLink(url="https://t.me/school", text="\U0001f517"))
    blank = _media_stories._link_sticker(StoryLink(url="https://t.me/school", text="  "))

    assert (sticker.label, blank.label) == ("t.me", "t.me")


def test_an_undrawable_host_falls_back_to_a_plain_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An IDN host on a machine with no font for its script: the story still gets a plate.
    monkeypatch.setattr(
        "core.telegram_client._media_stories.can_render_label", lambda text: text.isascii()
    )

    sticker = _media_stories._link_sticker(StoryLink(url="https://пример.рф/a", text=""))

    assert sticker.label == "LINK"


@pytest.mark.asyncio
async def test_an_unrenderable_sticker_label_surfaces_a_stable_code(
    story_client: _StoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("core.telegram_client._story_stickers._font", lambda *_: None)

    result = await execute(
        "acc",
        PostStory(
            filename="s.jpg",
            content=_jpeg((400, 500), (9, 9, 9)),
            media_kind="image",
            link_stickers=_loose([{"url": "example.com", "label": "Go", "x": 50, "y": 50}]),
        ),
    )

    assert (result.status, result.error_type, result.error_message) == (
        "failed",
        "StoryStickerError",
        "story_link_label_unrenderable",
    )
    assert not any(isinstance(r, SendStoryRequest) for r in story_client.requests)


@pytest.mark.asyncio
async def test_video_stickers_go_to_ffmpeg_as_an_overlay_png(
    story_client: _StoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, bytes | None] = {}

    async def fake_normalise(
        _content: bytes, *, overlay_png: bytes | None = None
    ) -> tuple[bytes, bytes, float, int, int]:
        captured["overlay"] = overlay_png
        return b"video", b"thumb", 3.0, 720, 1280

    monkeypatch.setattr(_media_stories, "normalize_story_video_for_telegram", fake_normalise)

    await execute(
        "acc",
        PostStory(
            filename="s.mp4",
            content=b"raw",
            media_kind="video",
            link_stickers=_loose([{"url": "example.com", "label": "Go", "x": 50, "y": 60}]),
        ),
    )

    overlay = captured["overlay"]
    assert overlay is not None
    with Image.open(BytesIO(overlay)) as png:
        assert png.size == (720, 1280)
    areas = story_client.sent().media_areas
    assert areas is not None
    assert (areas[0].coordinates.x, areas[0].coordinates.y) == (50, 60)


@pytest.mark.asyncio
async def test_a_video_without_stickers_has_no_overlay(
    story_client: _StoryClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[bytes | None] = []

    async def fake_normalise(
        _content: bytes, *, overlay_png: bytes | None = None
    ) -> tuple[bytes, bytes, float, int, int]:
        captured.append(overlay_png)
        return b"video", b"thumb", 3.0, 720, 1280

    monkeypatch.setattr(_media_stories, "normalize_story_video_for_telegram", fake_normalise)

    await execute("acc", PostStory(filename="s.mp4", content=b"raw", media_kind="video"))

    assert captured == [None]
    assert story_client.sent().media_areas is None
