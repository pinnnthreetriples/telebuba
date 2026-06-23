"""Cross-layer wiring guard: real listener (core) ↔ real engine (services).

The listener and the engine each carry their own copy of ``NewPostEvent`` /
``_listener.py`` in the PR stack; only when both agree on ``is_forward`` does the
forward filter work. This test builds a forwarded event through the REAL listener
handler and feeds it to the REAL engine filter, so a merge that drops ``is_forward``
from either side fails here instead of silently commenting on forwarded posts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from core.telegram_client._listener import _make_handler
from services.neurocomment import engine

if TYPE_CHECKING:
    from schemas.telegram_actions import NewPostEvent


@pytest.mark.asyncio
async def test_listener_forward_flag_is_honored_by_engine_filter() -> None:
    captured: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        captured.append(event)

    handler = _make_handler({-100: "@news"}, on_post)
    message = MagicMock(id=7, message="a real post body", media=None, post=True, fwd_from=object())
    await handler(MagicMock(message=message, chat_id=-100))

    assert captured[0].is_forward is True
    assert engine._filter_reason(captured[0]) == "forward"


@pytest.mark.asyncio
async def test_listener_non_forward_is_not_filtered_as_forward() -> None:
    captured: list[NewPostEvent] = []

    async def on_post(event: NewPostEvent) -> None:
        captured.append(event)

    handler = _make_handler({-100: "@news"}, on_post)
    message = MagicMock(id=8, message="a real post body", media=None, post=True, fwd_from=None)
    await handler(MagicMock(message=message, chat_id=-100))

    assert captured[0].is_forward is False
    assert engine._filter_reason(captured[0]) is None
