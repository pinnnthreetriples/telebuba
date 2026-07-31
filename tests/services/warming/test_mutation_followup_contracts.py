"""Behavioral contracts added from the final warming survivor review."""

from __future__ import annotations

import pytest

from core.db import create_account, save_warming_settings, upsert_warming_state
from schemas.accounts import AccountCreate
from schemas.warming import (
    ActivityPersona,
    StartWarmingRequest,
    StopWarmingRequest,
    WarmingState,
    WarmingStateWrite,
)
from services import warming
from services.warming import _runtime
from tests.services.warming._support import _fake_loop


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prior_state", "expected_persona"),
    [
        pytest.param("sleeping", "calm", id="restart-carries-in-flight-persona"),
        pytest.param("idle", "active", id="fresh-start-applies-requested-persona"),
    ],
)
async def test_start_persona_respects_the_lifecycle_boundary(
    monkeypatch: pytest.MonkeyPatch,
    prior_state: WarmingState,
    expected_persona: ActivityPersona,
) -> None:
    """Restart keeps an in-flight plan; a completed stint accepts a new plan."""
    monkeypatch.setattr(_runtime, "_warming_loop", _fake_loop)
    await create_account(AccountCreate(account_id="acc-1"))
    await save_warming_settings(
        inter_account_chat=False,
        reactions_enabled=False,
        enforce_readiness=False,
        gemini_api_key="",
    )
    await upsert_warming_state(
        WarmingStateWrite(
            account_id="acc-1",
            state=prior_state,
            activity_persona="calm",
        ),
    )

    try:
        card = await warming.start_warming(
            StartWarmingRequest(account_id="acc-1", activity_persona="active"),
        )

        assert card.state == "active"
        assert card.activity_persona == expected_persona
    finally:
        await warming.stop_warming(StopWarmingRequest(account_id="acc-1"))
