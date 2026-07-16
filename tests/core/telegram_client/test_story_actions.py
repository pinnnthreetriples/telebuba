"""Story image composition and write-action tests."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image
from telethon.tl.functions.stories import (
    CanSendStoryRequest,
    DeleteStoriesRequest,
    SendStoryRequest,
    TogglePinnedRequest,
)

from core.telegram_client import execute
from schemas.telegram_actions import PostStory, RemoveStory, ToggleStoryPinned
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
