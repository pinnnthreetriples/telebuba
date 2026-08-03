"""Caption-less media posts: the vision path, and everything that still skips.

Live, over four days, the engine published 39 comments and threw away 85 posts for
``media_no_caption`` alone. A caption-less PHOTO is now downloaded and shown to the
model instead; the rest keep skipping, each under its own reason so the operator can
see what is genuinely still on the table. These tests pin both halves, plus the two
ways the download can let us down (gone / too big) — neither of which may end in a
crash or in a comment about nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import fetch_comment, list_recent_logs
from schemas.gemini import GeminiResult
from schemas.telegram_actions import NewPostEvent, PostImageResult
from services.neurocomment import _seams, engine
from tests.services.neurocomment.engine_support import _CommentStub, _make_campaign, _patch_io

if TYPE_CHECKING:
    from schemas.gemini import GeminiRequest

pytestmark = pytest.mark.usefixtures("isolate_engine")

_IMAGE = "aW1n"


def _photo_post(post_id: int = 10, text: str = "") -> NewPostEvent:
    return NewPostEvent(channel="@chan", post_id=post_id, text=text, media_kind="photo")


class _ImageDownloads:
    """Answers the gateway's photo fetch with a canned result, recording every call."""

    def __init__(self, result: PostImageResult) -> None:
        self._result = result
        self.calls: list[tuple[str, str, int, int]] = []

    async def download_post_image(
        self, account_id: str, channel: str, post_id: int, max_bytes: int
    ) -> PostImageResult:
        self.calls.append((account_id, channel, post_id, max_bytes))
        return self._result


class _CapturingGen:
    """Records the Gemini requests, so a test can assert what actually rode along."""

    def __init__(self, text: str = "what a bridge") -> None:
        self.text = text
        self.requests: list[GeminiRequest] = []

    async def generate_text(self, request: GeminiRequest) -> GeminiResult:
        self.requests.append(request)
        return GeminiResult(status="ok", text=self.text)


def _patch_download(monkeypatch: pytest.MonkeyPatch, result: PostImageResult) -> _ImageDownloads:
    downloads = _ImageDownloads(result)
    monkeypatch.setattr(_seams, "download_post_image", downloads.download_post_image)
    return downloads


async def _skip_reasons() -> list[str]:
    return [
        str(entry.extra["reason"])
        for entry in await list_recent_logs(limit=100)
        if entry.event == "neurocomment_post_skipped"
    ]


# --------------------------------------------------------------------------- #
# The win: a caption-less photo now earns a comment
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_captionless_photo_is_commented_on_from_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub(status="ok", message_id=999)
    gen = _CapturingGen()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "generate_text", gen.generate_text)
    downloads = _patch_download(monkeypatch, PostImageResult(image_b64=_IMAGE))

    await engine.handle_new_post(_photo_post())

    assert [action.action_type for _, action in comment.calls] == ["comment_on_post"]
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "posted"
    assert record.comment_text == "what a bridge"
    # Exactly one download — on the commenting account, for this post, under the cap.
    assert downloads.calls == [("acc-1", "@chan", 10, settings.neurocomment.vision_max_image_bytes)]
    # The image rode along, and the prompt says so instead of fencing an empty post.
    assert gen.requests[0].image_b64 == _IMAGE
    assert "attached image" in gen.requests[0].prompt
    assert "<post>" not in gen.requests[0].prompt


@pytest.mark.asyncio
async def test_photo_with_a_caption_stays_a_plain_text_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A captioned photo already worked — it must not start paying for a download."""
    await _make_campaign("@chan", "acc-1")
    gen = _CapturingGen()
    _patch_io(monkeypatch, comment=_CommentStub())
    monkeypatch.setattr(_seams, "generate_text", gen.generate_text)
    downloads = _patch_download(monkeypatch, PostImageResult(image_b64=_IMAGE))

    await engine.handle_new_post(_photo_post(text="look at this bridge"))

    assert downloads.calls == []
    assert gen.requests[0].image_b64 is None
    assert "<post>\nlook at this bridge\n</post>" in gen.requests[0].prompt


# --------------------------------------------------------------------------- #
# Degradation: no image → the old skip, never a crash and never an empty comment
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (PostImageResult(reason="unavailable"), "media_unavailable"),
        (PostImageResult(reason="too_large"), "media_too_large"),
    ],
)
async def test_failed_download_skips_the_post_and_spends_it(
    monkeypatch: pytest.MonkeyPatch,
    result: PostImageResult,
    reason: str,
) -> None:
    """The attempt costs the POST, not the account — and it costs it exactly once.

    Releasing the claim (a DELETE) refunded the whole attempt, so the same post could be
    re-delivered and re-run the eleven-odd reads, the account pick and the fetch again,
    for free, as often as the channel cared to make it happen. ``failed`` keeps the row
    that ``claim_comment`` wins against, so the second delivery is a no-op — while the
    quota (which counts only ``claimed``/``posted``) still charges the account nothing
    for a picture the gateway would not hand over.
    """
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    gen = _CapturingGen()
    _patch_io(monkeypatch, comment=comment)
    monkeypatch.setattr(_seams, "generate_text", gen.generate_text)
    downloads = _patch_download(monkeypatch, result)

    await engine.handle_new_post(_photo_post())
    await engine.handle_new_post(_photo_post())

    # Nothing generated (no comment about nothing) and nothing posted...
    assert gen.requests == []
    assert comment.calls == []
    assert await _skip_reasons() == [reason]
    # ...the post is terminal, so the second delivery never reaches the gateway again...
    assert len(downloads.calls) == 1
    record = await fetch_comment("@chan", 10)
    assert record is not None
    assert record.status == "failed"
    # ...and it is 'failed', not 'claimed': the day-cap slot comes straight back.
    assert record.comment_text is None


# --------------------------------------------------------------------------- #
# What still skips — and never pays for a download to find out
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        # Video / document / poll / sticker / link preview: nothing readable to look at.
        ("other", "media_no_image"),
        # An album item is a duplicate of the head we already commented on, not a loss.
        ("album", "media_album_item"),
    ],
)
async def test_unreadable_media_skips_with_its_own_reason(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    reason: str,
) -> None:
    await _make_campaign("@chan", "acc-1")
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    downloads = _patch_download(monkeypatch, PostImageResult(image_b64=_IMAGE))

    await engine.handle_new_post(
        NewPostEvent(channel="@chan", post_id=11, text="", media_kind=kind),  # ty: ignore[invalid-argument-type]
    )

    assert comment.calls == []
    assert downloads.calls == []
    assert await _skip_reasons() == [reason]


@pytest.mark.asyncio
async def test_zero_size_cap_turns_the_vision_path_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator's off-switch: back to the pre-vision skip, before any account is picked."""
    await _make_campaign("@chan", "acc-1")
    monkeypatch.setattr(settings.neurocomment, "vision_max_image_bytes", 0)
    comment = _CommentStub()
    _patch_io(monkeypatch, comment=comment)
    downloads = _patch_download(monkeypatch, PostImageResult(image_b64=_IMAGE))

    await engine.handle_new_post(_photo_post())

    assert comment.calls == []
    assert downloads.calls == []
    assert await _skip_reasons() == ["media_no_caption"]
