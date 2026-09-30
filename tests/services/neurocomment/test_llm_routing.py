"""Which LLM writes the comment: the operator's text LLM, image posts included.

``deepseek-flash`` reads images, so a caption-less photo post goes to whichever
provider the Settings page chose — with the picture attached, or the comment would
be confidently about nothing. A chosen provider without a key hands over to the other
one: this is the hot path for every comment a campaign writes, so "no key" must mean
"carry on with the other model" rather than "stop commenting".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import save_warming_settings
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
    monkeypatch.setattr(settings.deepseek, "model", "deepseek-flash")


async def _download_photo(
    _account_id: str, _channel: str, _post_id: int, _max_bytes: int
) -> PostImageResult:
    return PostImageResult(image_b64=_IMAGE)


@pytest.mark.asyncio
async def test_a_text_post_is_written_by_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default choice: DeepSeek, which wrote every text before the choice existed."""
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
    assert request.model == "deepseek-flash"
    assert request.image_b64 is None
    assert comment.calls


@pytest.mark.asyncio
async def test_a_photo_post_goes_to_deepseek_with_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek reads images now, so a photo post no longer needs Gemini."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "download_post_image", _download_photo)
    _use_deepseek_key(monkeypatch)
    deepseek = _CapturingGen("what a bridge")
    _patch_providers(
        monkeypatch, gemini=_ExplodingGen("Gemini").generate_text, deepseek=deepseek.generate_text
    )

    await engine.handle_new_post(
        NewPostEvent(channel="@chan", post_id=2, text="", media_kind="photo"),
    )

    assert len(deepseek.requests) == 1
    # Not merely "DeepSeek was used": the image has to have actually ridden along.
    assert deepseek.requests[0].image_b64 == _IMAGE


@pytest.mark.asyncio
async def test_a_chosen_gemini_writes_even_with_a_deepseek_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's choice wins over which keys happen to be set."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "download_post_image", _download_photo)
    _use_deepseek_key(monkeypatch)
    await save_warming_settings(gemini_api_key=None, text_llm_provider="gemini")
    gemini = _CapturingGen()
    _patch_providers(
        monkeypatch, gemini=gemini.generate_text, deepseek=_ExplodingGen("DeepSeek").generate_text
    )

    await engine.handle_new_post(
        NewPostEvent(channel="@chan", post_id=3, text="", media_kind="photo"),
    )

    assert len(gemini.requests) == 1
    assert gemini.requests[0].image_b64 == _IMAGE
    assert comment.calls


@pytest.mark.asyncio
async def test_without_a_deepseek_key_gemini_still_writes_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chosen DeepSeek with no key is "use the other one", not "broken"."""
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(settings.deepseek, "api_key", "")
    gemini = _CapturingGen()
    _patch_providers(
        monkeypatch, gemini=gemini.generate_text, deepseek=_ExplodingGen("DeepSeek").generate_text
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=4, text="a real post"))

    assert len(gemini.requests) == 1
    assert comment.calls


@pytest.mark.asyncio
async def test_a_chosen_gemini_without_its_key_hands_over_to_deepseek(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    _use_deepseek_key(monkeypatch)
    monkeypatch.setattr(settings.gemini, "api_key", "")
    await save_warming_settings(gemini_api_key="", text_llm_provider="gemini")
    deepseek = _CapturingGen()
    _patch_providers(
        monkeypatch, gemini=_ExplodingGen("Gemini").generate_text, deepseek=deepseek.generate_text
    )

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=5, text="a real post"))

    assert len(deepseek.requests) == 1
    assert comment.calls
