"""Discovery stage 2 — the linked-group cache's freshness window.

Split from ``test_discovery_qualify`` (700-line test cap); the helpers stay there, this
module borrows them.
"""

from __future__ import annotations

import pytest

from core.config import settings
from core.repositories.neurocomment import upsert_linked_group
from services.neurocomment import _seams
from services.neurocomment._discovery_qualify import run_qualification
from tests.services.neurocomment.discovery_support import (
    ReadRecorder,
    pool_of,
    search_request,
    work_for,
)
from tests.services.neurocomment.test_discovery_qualify import _backdate, _seed, _verdict

pytestmark = pytest.mark.usefixtures("isolate_discovery")


@pytest.mark.asyncio
async def test_an_unparseable_cache_stamp_is_treated_as_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive, and reachable: the column is text a legacy row could have written."""
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    campaign_id = await _seed("garbled")
    await upsert_linked_group("garbled", -100, comments_enabled=True)
    await _backdate("garbled", "not-a-timestamp")

    await run_qualification(campaign_id, pool_of(), search_request(), work_for(pool_of()))

    assert len(reader.calls) == 1


@pytest.mark.asyncio
async def test_zero_ttl_disables_the_cache_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = ReadRecorder(linked=lambda _action: _verdict(enabled=True))
    monkeypatch.setattr(_seams, "execute_read", reader)
    monkeypatch.setattr(settings.neurocomment, "discovery_linked_group_ttl_hours", 0)
    campaign_id = await _seed("known")
    await upsert_linked_group("known", -100, comments_enabled=True)

    await run_qualification(campaign_id, pool_of(), search_request(), work_for(pool_of()))

    assert len(reader.calls) == 1
