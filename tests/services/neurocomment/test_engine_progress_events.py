"""The two positive progress rows the dashboard's pipeline rail is built on.

Every other row the per-post path writes reports a MISS — a skipped post, a busy account,
an exhausted generation — so before these two the rail's «Новый пост» and «Генерация»
steps could never light and it was pinned to a hardcoded position instead.

The SPA maps both codes in ``frontend/src/pages/neurocomment/ui/pipelineStage.ts``.
Nothing can enforce that link across the two languages, which is exactly why the codes are
asserted here: rename one on either side and the rail silently freezes again.

Kept out of ``test_engine_pipeline.py`` only because that file is at the 700-line cap.
"""

from __future__ import annotations

import pytest

from core.db import list_recent_logs
from schemas.telegram_actions import NewPostEvent
from services.neurocomment import engine
from tests.services.neurocomment.engine_support import (
    _CommentStub,
    _make_campaign,
    _patch_io,
)

pytestmark = pytest.mark.usefixtures("isolate_engine")


async def _events() -> list[str]:
    return [row.event for row in await list_recent_logs(50)]


@pytest.mark.asyncio
async def test_happy_path_logs_the_two_rail_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub(status="ok", message_id=1))

    await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hello world"))

    events = await _events()
    assert "neurocomment_post_received" in events
    assert "neurocomment_generation_started" in events


@pytest.mark.asyncio
async def test_a_filtered_post_still_records_its_arrival(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arrival is logged ABOVE every gate, and this is what pins it there.

    A forward proves the channel is alive and was seen just as well as a post we answer, so
    «Новый пост» has to light for it and «Фильтр» take it from there. Move the ``log_event``
    below ``_filters.filter_reason`` and the happy-path test above stays green while the
    rail stops acknowledging the commonest kind of post there is.
    """
    await _make_campaign("@chan", "acc-1")
    _patch_io(monkeypatch, comment=_CommentStub())

    await engine.handle_new_post(
        NewPostEvent(channel="@chan", post_id=11, text="real text", is_forward=True),
    )

    events = await _events()
    assert "neurocomment_post_received" in events
    assert "neurocomment_post_skipped" in events
    # Nothing was claimed, so no generation began.
    assert "neurocomment_generation_started" not in events
