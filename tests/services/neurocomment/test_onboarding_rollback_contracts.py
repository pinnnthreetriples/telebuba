"""Repeated onboarding and partial-failure rollback contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from schemas.neurocomment import AccountChannelOnboarding
from services.neurocomment import _onboard_channel, _seams, _state, onboarding

if TYPE_CHECKING:
    from schemas.neurocomment_progress import OnboardingProgressEvent

pytestmark = pytest.mark.usefixtures("isolate_onboarding")


def _context(
    *, accounts: list[str], ready: set[tuple[str, str]]
) -> tuple[_onboard_channel.OnboardContext, list[OnboardingProgressEvent]]:
    events: list[OnboardingProgressEvent] = []
    ctx = _onboard_channel.OnboardContext(
        accounts=accounts,
        already_ready=ready,
        outcomes=[],
        solver_enabled=False,
        on_progress=events.append,
        report=events.append,
    )
    return ctx, events


@pytest.mark.asyncio
async def test_fully_ready_channel_is_idempotent_without_gateway_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, events = _context(accounts=["a", "b"], ready={("a", "@c"), ("b", "@c")})
    resolve = AsyncMock()
    monkeypatch.setattr(onboarding, "_resolve_group_for_join", resolve)

    joined = await _onboard_channel.onboard_channel("@c", ctx, joined_once=False)

    assert joined is False
    resolve.assert_not_awaited()
    assert [(o.account_id, o.state) for o in ctx.outcomes] == [("a", "ready"), ("b", "ready")]
    assert [event.code for event in events] == ["channel_all_ready"]


@pytest.mark.asyncio
async def test_transient_resolve_failure_does_not_rollback_existing_ready_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, _events = _context(accounts=["ready", "new"], ready={("ready", "@c")})

    async def resolve(
        accounts: list[str], channel: str, outcomes: list[AccountChannelOnboarding], **_kw: object
    ) -> None:
        assert accounts == ["new"]
        outcomes.append(
            AccountChannelOnboarding(
                account_id="new", channel=channel, state="failed", reason="resolve_failed"
            )
        )

    monkeypatch.setattr(onboarding, "_resolve_group_for_join", resolve)

    joined = await _onboard_channel.onboard_channel("@c", ctx, joined_once=True)

    assert joined is True
    assert [(o.account_id, o.state) for o in ctx.outcomes] == [
        ("new", "failed"),
        ("ready", "ready"),
    ]


@pytest.mark.asyncio
async def test_challenge_backoff_persists_not_ready_without_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onboarding, "fetch_readiness", AsyncMock(return_value=None))
    monkeypatch.setattr(_state, "is_channel_in_challenge_backoff", lambda *_a: True)
    write = AsyncMock()
    execute = AsyncMock()
    monkeypatch.setattr(onboarding, "upsert_readiness", write)
    monkeypatch.setattr(_seams, "execute", execute)

    outcome = await onboarding._join_and_classify("account", "@c", 123, solver_enabled=True)

    assert outcome.state == "bot_challenge_backoff"
    write.assert_awaited_once_with("account", "@c", joined=False, captcha_passed=False, ready=False)
    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unexpected_pair_failure_becomes_local_failed_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        onboarding, "_join_and_classify", AsyncMock(side_effect=TimeoutError("join"))
    )
    log = AsyncMock()
    monkeypatch.setattr(onboarding, "log_event", log)

    outcome = await onboarding._join_pair_safely("account", "@c", 123, solver_enabled=False)

    assert outcome.model_dump() == {
        "account_id": "account",
        "channel": "@c",
        "state": "failed",
        "reason": "TimeoutError",
    }
    log.assert_awaited_once()


def test_pins_select_only_explicit_channel_while_empty_pin_selects_all() -> None:
    ctx = _onboard_channel.OnboardContext(
        accounts=["all", "a-only", "b-only"],
        already_ready=set(),
        outcomes=[],
        solver_enabled=False,
        on_progress=None,
        report=lambda _event: None,
        pins={"all": [], "a-only": ["@a"], "b-only": ["@b"]},
    )

    assert ctx.accounts_for("@a") == ["all", "a-only"]
    assert ctx.accounts_for("@b") == ["all", "b-only"]
