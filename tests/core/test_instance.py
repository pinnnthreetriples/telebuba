"""Only the fleet owner's computer builds Telegram clients; any other refuses before connecting."""

from __future__ import annotations

import socket

import pytest

from core import instance
from core.config import settings
from core.instance import FleetOwnerRefusedError
from core.telegram_client import _client
from schemas.device_fingerprint import TelegramClientProfile


@pytest.mark.parametrize(
    ("owner", "expected"),
    [("", True), ("THIS-HOST", True), ("this-host", True), ("other-pc", False)],
)
def test_who_is_the_fleet_owner(
    monkeypatch: pytest.MonkeyPatch, owner: str, *, expected: bool
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "this-host")
    monkeypatch.setattr(settings.instance, "fleet_owner_host", owner)

    assert instance.is_fleet_owner() is expected


def test_another_computer_never_builds_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "laptop")
    monkeypatch.setattr(settings.instance, "fleet_owner_host", "main-pc")
    built: list[object] = []
    monkeypatch.setattr(_client, "GuardedTelegramClient", lambda *a, **_k: built.append(a))

    with pytest.raises(FleetOwnerRefusedError):
        _client.create_telegram_client(TelegramClientProfile.model_construct())

    # A ConnectionError: every caller already reads it as "account unreachable".
    assert issubclass(FleetOwnerRefusedError, ConnectionError)
    assert built == []
