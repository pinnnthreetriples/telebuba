"""Fast behavioral contracts for the neurocomment onboarding scheduler."""

from __future__ import annotations

import pytest

from schemas.neurocomment import CampaignList
from services.neurocomment import _runtime


@pytest.mark.asyncio
async def test_onboarding_without_queued_trigger_runs_one_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An idle scheduler must finish instead of starting an unrequested pass."""
    scans = 0

    async def list_campaigns_once() -> CampaignList:
        nonlocal scans
        scans += 1
        assert scans == 1, "onboarding repeated without a queued trigger"
        return CampaignList(campaigns=[])

    monkeypatch.setattr(_runtime, "list_campaigns", list_campaigns_once)
    monkeypatch.setattr(_runtime, "_ONBOARD_RERUN", False)

    await _runtime._onboard_active_campaigns(None)

    assert scans == 1
