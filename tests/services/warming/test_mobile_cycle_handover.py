"""A pod the operator takes while a warming visit ends is theirs: never parked or stopped.

Reuses the fake pod of ``test_mobile_cycle``; only who holds the phone changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from services.warming import _mobile_cycle
from tests.services.warming._mobile_support import (
    _iterate_on,
    _Pod,
    _stops_done,
)

if TYPE_CHECKING:
    from schemas.device_farm import PhoneStatus


@pytest.fixture
def mobile_pod(request: pytest.FixtureRequest) -> _Pod:
    """``test_mobile_cycle``'s fake pod, its account ready, Telethon never called."""
    request.getfixturevalue("telethon")
    return request.getfixturevalue("pod")


@pytest.mark.parametrize("signed_in", [True, False])
@pytest.mark.asyncio
async def test_a_visit_ending_during_the_operators_open_leaves_the_pod_to_it(
    mobile_pod: _Pod, monkeypatch: pytest.MonkeyPatch, *, signed_in: bool
) -> None:
    mobile_pod.signed_in = signed_in  # parked after a good visit, stopped after a failed one
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)
    status: list[PhoneStatus | None] = [None]

    async def run(*_args: object, **_kwargs: object) -> object:
        status[0] = "starting"  # the operator's open takes the lock as the visit ends
        return mobile_pod.activity

    monkeypatch.setattr(_mobile_cycle, "run_mobile_activity", run)
    monkeypatch.setattr(_mobile_cycle, "phone_activity", lambda _account_id: status[0])
    if not signed_in:
        status[0] = "starting"  # a failed visit never reaches the session

    await _iterate_on("mobile")
    await _stops_done()

    assert mobile_pod.calls == ["open:background"], "the operator's pod is never paused or stopped"
    assert not _mobile_cycle._RUNNING, "and it holds no warming slot"
    assert not _mobile_cycle._PARKED


@pytest.mark.asyncio
async def test_a_pod_the_operator_opened_meanwhile_is_not_parked(
    mobile_pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)
    monkeypatch.setattr(_mobile_cycle, "operator_holds", lambda _account_id: True)

    await _iterate_on("mobile")
    await _stops_done()

    assert "park" not in mobile_pod.calls
    assert "stop" not in mobile_pod.calls
    assert not _mobile_cycle._RUNNING


@pytest.mark.asyncio
async def test_a_prepared_pod_the_operator_opened_meanwhile_is_left_to_them(
    mobile_pod: _Pod, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.emulator, "pause_ttl_seconds", 1)
    monkeypatch.setattr(_mobile_cycle, "operator_holds", lambda _account_id: True)

    assert await _mobile_cycle.prepare_pod("acc-1") is True
    await _stops_done()

    assert mobile_pod.calls == ["open:background"]
    assert not _mobile_cycle._RUNNING
