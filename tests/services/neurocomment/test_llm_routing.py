"""Which LLM writes the comment: DeepSeek for text, Gemini for anything with an image.

``deepseek-v4-flash`` is text-only — DeepSeek publishes ``input_modalities: ["text"]``
— so the split is not a preference the operator tunes but a capability boundary. Send
a caption-less photo post to DeepSeek and the picture is silently dropped: the model
answers about the caption it never saw, and the comment is confidently about nothing.
Nothing downstream would notice, which is why the routing is pinned here rather than
left to the two ``settings.deepseek`` reads that implement it.

The third test is the fallback. The key is deployment config with no UI switch, so an
empty one has to mean "carry on with Gemini" rather than "stop commenting" — this is
the hot path for every comment a campaign writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from schemas.gemini import GeminiResult
from schemas.telegram_actions import NewPostEvent, PostImageResult
from services.neurocomment import _seams, engine
from tests.services.neurocomment.engine_support import _CommentStub, _make_campaign, _patch_io

if TYPE_CHECKING:
    from schemas.gemini import GeminiRequest

pytestmark = pytest.mark.usefixtures("isolate_engine")

_IMAGE = "aW1n"


class _CapturingGen:
    """Records every request it is handed, so a test can name the provider that got it."""

    def __init__(self, text: str = "a nice comment") -> None:
        self.text = text
        self.requests: list[GeminiRequest] = []

    async def generate_text(self, request: GeminiRequest) -> GeminiResult:
        self.requests.append(request)
        return GeminiResult(status="ok", text=self.text)


class _ExplodingGen:
    """The provider that must NOT be called; fails the test loudly if it is."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def generate_text(self, _request: GeminiRequest) -> GeminiResult:
        msg = f"{self.name} was asked to generate, but this post belongs to the other provider"
        raise AssertionError(msg)


def _patch_providers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    gemini: object,
    deepseek: object,
) -> None:
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "generate_text_deepseek", deepseek)


def _use_deepseek_key(monkeypatch: pytest.MonkeyPatch, key: str = "ds-key") -> None:
    monkeypatch.setattr(settings.deepseek, "api_key", key)
    monkeypatch.setattr(settings.deepseek, "model", "deepseek-v4-flash")


async def _download_photo(
    _account_id: str, _channel: str, _post_id: int, _max_bytes: int
) -> PostImageResult:
    return PostImageResult(image_b64=_IMAGE)


@pytest.mark.asyncio
async def test_a_text_post_is_written_by_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordinary case: no image, so the text-only model does the writing."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    _use_deepseek_key(monkeypatch)
    deepseek = _CapturingGen()
    _patch_providers(
        monkeypatch, gemini=_ExplodingGen("Gemini").generate_text, deepseek=deepseek.generate_text
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=1, text="a real post"))

    assert len(deepseek.requests) == 1
    request = deepseek.requests[0]
    assert request.api_key == "ds-key"
    assert request.model == "deepseek-v4-flash"
    # The picture-carrying field is what the whole split turns on.
    assert request.image_b64 is None
    assert comment.calls


@pytest.mark.asyncio
async def test_a_photo_post_stays_on_gemini_even_with_deepseek_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The capability boundary: DeepSeek cannot see, so the image never goes there."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "download_post_image", _download_photo)
    _use_deepseek_key(monkeypatch)
    gemini = _CapturingGen("what a bridge")
    _patch_providers(
        monkeypatch, gemini=gemini.generate_text, deepseek=_ExplodingGen("DeepSeek").generate_text
    )

    await engine.handle_new_post(
        NewPostEvent(channel="@chan", post_id=2, text="", media_kind="photo"),
    )

    assert len(gemini.requests) == 1
    # Not merely "Gemini was used": the image has to have actually ridden along, or
    # the routing would be right for a reason that stops being true.
    assert gemini.requests[0].image_b64 == _IMAGE


@pytest.mark.asyncio
async def test_without_a_deepseek_key_gemini_still_writes_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset key is "off", not "broken" — every existing deployment is this one."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(settings.deepseek, "api_key", "")
    gemini = _CapturingGen()
    _patch_providers(
        monkeypatch, gemini=gemini.generate_text, deepseek=_ExplodingGen("DeepSeek").generate_text
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=3, text="a real post"))

    assert len(gemini.requests) == 1
    assert comment.calls
