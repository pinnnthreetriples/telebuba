"""The gateway stamps the calling domain onto every event name ``execute`` writes.

One ``logs`` table, per-domain feeds filtered by ``event LIKE 'prefix%'``: a bare
``telegram_*`` name is invisible in its caller's feed and leaks into every other.
``domain`` is bound at the service seam, so these tests pass it explicitly.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from telethon import errors

from core.telegram_client import execute
from core.telegram_client._profile import _DEAD_SESSION_ERRORS
from schemas.telegram_actions import JoinChannel
from tests.core.telegram_client.helpers import patch_action_client

# Pins the dead-session case below to that ``except`` arm rather than the generic tail.
assert errors.AuthKeyUnregisteredError in _DEAD_SESSION_ERRORS


class _FakeClient:
    """Pooled-client stand-in: raises ``exc`` on the request, or succeeds."""

    def __init__(self, exc: Exception | None) -> None:
        self._exc = exc

    async def connect(self) -> None:
        return None

    async def __call__(self, _request: object) -> None:
        if self._exc is not None:
            raise self._exc


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the event names written by both gateway modules, in order."""
    names: list[str] = []

    async def _fake_log(_level: str, event: str, **_kwargs: object) -> None:
        names.append(event)

    for module in ("_actions", "_action_results"):
        monkeypatch.setattr(f"core.telegram_client.{module}.log_event", _fake_log)
    return names


@pytest.mark.asyncio
# One bound domain is enough: ``event_name`` formats any string identically, and WHICH
# domain each service binds is pinned by ``tests/services/test_gateway_domain_seams.py``.
@pytest.mark.parametrize("domain", [None, "warming"])
@pytest.mark.parametrize(
    ("exc", "bare_name"),
    [
        (None, "telegram_join_channel"),
        (RuntimeError("boom"), "telegram_join_channel_failed"),
        (errors.FloodWaitError(request=None, capture=7), "telegram_join_channel_flood_wait"),
        (
            errors.UserAlreadyParticipantError(request=None),
            "telegram_join_channel_already_participant",
        ),
        # Dead session lands on the same name as any other generic failure, but it is
        # its own ``except`` arm — pinned separately so it cannot lose ``domain``.
        (errors.AuthKeyUnregisteredError(request=None), "telegram_join_channel_failed"),
    ],
)
async def test_action_outcome_event_carries_the_bound_domain(
    monkeypatch: pytest.MonkeyPatch,
    domain: str | None,
    exc: Exception | None,
    bare_name: str,
) -> None:
    patch_action_client(monkeypatch, _FakeClient(exc))
    events = _capture_events(monkeypatch)

    await execute("acc-domain", JoinChannel(channel="@hot"), domain=domain)

    assert events == [bare_name if domain is None else f"{domain}_{bare_name}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", [None, "neurocomment"])
async def test_action_unavailable_event_carries_the_bound_domain(
    monkeypatch: pytest.MonkeyPatch,
    domain: str | None,
) -> None:
    """The one fixed gateway name takes the prefix too — it is an action outcome."""

    async def _failing_get_client(_account_id: str) -> object:
        msg = "socket closed"
        raise ConnectionError(msg)

    async def _fake_fetch(account_id: str) -> MagicMock:
        return MagicMock(session_name=account_id)

    monkeypatch.setattr("core.telegram_client._actions.get_client", _failing_get_client)
    monkeypatch.setattr("core.telegram_client._actions.fetch_account", _fake_fetch)
    events = _capture_events(monkeypatch)

    await execute("acc-domain", JoinChannel(channel="@hot"), domain=domain)

    prefix = "" if domain is None else f"{domain}_"
    assert events == [f"{prefix}telegram_action_unavailable"]
