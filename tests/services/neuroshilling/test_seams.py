"""The neuroshilling seam: the run fence and where the pacer sits.

Two properties are worth a test each, and both are ordering properties that no
type or signature can express: the run generation is checked on BOTH sides of an
external call, and the pacing sleep happens OUTSIDE the account lifecycle lock.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

import services.warming as warming_module
from core.config import settings
from schemas.gemini import GeminiRequest
from schemas.telegram_actions import CheckBannedInChannel, PostComment
from services.neuroshilling import _seams

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_REQUEST = GeminiRequest(
    api_key="k",
    prompt="write",
    model="m",
    temperature=0.7,
    max_output_tokens=64,
)


@pytest.fixture
def trace(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the order of pacer, lock and gateway for one seam call."""
    events: list[str] = []

    @contextlib.asynccontextmanager
    async def _lock(account_id: str) -> AsyncIterator[None]:
        events.append(f"lock:{account_id}")
        yield
        events.append("unlock")

    async def _pace(key: str, gap: float) -> None:
        events.append(f"pace:{key}:{gap}")

    async def _execute(account_id: str, action: object, *, domain: str) -> str:  # noqa: ARG001
        events.append(f"execute:{domain}")
        return "sent"

    async def _execute_read(account_id: str, action: object) -> str:  # noqa: ARG001
        events.append("read")
        return "seen"

    monkeypatch.setattr(warming_module, "account_lock", _lock)
    monkeypatch.setattr(_seams, "await_send_slot", _pace)
    monkeypatch.setattr(_seams, "_gateway_execute", _execute)
    monkeypatch.setattr(_seams, "_gateway_execute_read", _execute_read)
    return events


@pytest.mark.asyncio
async def test_a_write_paces_before_it_takes_the_lifecycle_lock(trace: list[str]) -> None:
    """Sleeping inside ``account_lock`` would freeze Start/Stop for that account."""
    await _seams.execute("acc-1", PostComment(chat_id=5, text="hi"))

    gap = settings.neuroshilling.send_min_gap_seconds
    assert trace == [f"pace:acc-1:{gap}", "lock:acc-1", "execute:neuroshilling", "unlock"]


@pytest.mark.asyncio
async def test_a_read_takes_the_lock_but_is_not_paced(trace: list[str]) -> None:
    """The gate exists to space out what we PUBLISH; slowing reads buys nothing."""
    await _seams.execute_read("acc-1", CheckBannedInChannel(channel="@news"))

    assert trace == ["lock:acc-1", "read", "unlock"]


@pytest.mark.usefixtures("trace")
@pytest.mark.asyncio
async def test_without_a_run_scope_nothing_is_fenced() -> None:
    """Operator-driven calls are not part of a run and must not need one."""
    assert await _seams.execute("acc-1", PostComment(chat_id=5, text="hi")) == "sent"


@pytest.mark.asyncio
async def test_a_revoked_run_is_stopped_before_the_call(trace: list[str]) -> None:
    with (
        _seams.run_scope(lambda: False),
        pytest.raises(_seams.NeuroshillingRunRevokedError),
    ):
        await _seams.execute("acc-1", PostComment(chat_id=5, text="hi"))

    assert trace == []


@pytest.mark.asyncio
async def test_a_run_revoked_mid_flight_still_refuses_the_result(
    trace: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop landed while the send was in the air: its outcome is unknown, so stop."""
    live = [True]

    async def _execute(account_id: str, action: object, *, domain: str) -> str:  # noqa: ARG001
        trace.append("execute")
        live[0] = False
        return "sent"

    monkeypatch.setattr(_seams, "_gateway_execute", _execute)
    with (
        _seams.run_scope(lambda: live[0]),
        pytest.raises(_seams.NeuroshillingRunRevokedError),
    ):
        await _seams.execute("acc-1", PostComment(chat_id=5, text="hi"))

    assert "execute" in trace


@pytest.mark.asyncio
async def test_a_read_is_fenced_on_both_sides_too(trace: list[str]) -> None:
    with (
        _seams.run_scope(lambda: False),
        pytest.raises(_seams.NeuroshillingRunRevokedError),
    ):
        await _seams.execute_read("acc-1", CheckBannedInChannel(channel="@news"))

    assert trace == ["lock:acc-1"]


@pytest.mark.usefixtures("trace")
@pytest.mark.asyncio
async def test_the_run_scope_is_restored_when_the_block_ends() -> None:
    with contextlib.suppress(_seams.NeuroshillingRunRevokedError), _seams.run_scope(lambda: False):
        await _seams.execute("acc-1", PostComment(chat_id=5, text="hi"))

    assert await _seams.execute("acc-1", PostComment(chat_id=5, text="hi")) == "sent"


@pytest.mark.asyncio
async def test_generation_calls_are_fenced_by_the_same_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _generate(request: object) -> str:  # noqa: ARG001
        return "text"

    monkeypatch.setattr(_seams, "_generate_text_deepseek", _generate)
    monkeypatch.setattr(_seams, "_generate_text", _generate)

    assert await _seams.generate_text_deepseek(_REQUEST) == "text"
    assert await _seams.generate_text(_REQUEST) == "text"
    with _seams.run_scope(lambda: False):
        for call in (_seams.generate_text_deepseek, _seams.generate_text):
            with pytest.raises(_seams.NeuroshillingRunRevokedError):
                await call(_REQUEST)
