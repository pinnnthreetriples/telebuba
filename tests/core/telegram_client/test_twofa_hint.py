"""The hint Telegram shows at the login prompt must never contain the new password.

The request model refuses a TYPED hint that holds the password; a hint left out means
"keep the live one", which only the gateway can read, so the gateway refuses it too —
before any SRP work and before anything is written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from telethon.tl.functions.account import UpdatePasswordSettingsRequest

from core.telegram_client import execute
from schemas.telegram_actions import SetTwoFactorPassword
from tests.core.telegram_client._twofa_doubles import (
    PASSWORD,
    Password,
    RawClient,
    patch_srp,
)
from tests.core.telegram_client.helpers import patch_action_client

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult


@pytest.fixture
def stub_srp(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    return patch_srp(monkeypatch)


def _writes(client: RawClient) -> list[object]:
    return [r for r in client.requests if isinstance(r, UpdatePasswordSettingsRequest)]


async def _set(
    monkeypatch: pytest.MonkeyPatch, *, live_hint: str | None, typed: str | None
) -> tuple[ActionResult, RawClient]:
    client = RawClient(password=Password(has_password=True, hint=live_hint))
    patch_action_client(monkeypatch, client)
    result = await execute(
        "acc-2fa",
        SetTwoFactorPassword(current_password="old-one", new_password=PASSWORD, hint=typed),
    )
    return result, client


@pytest.mark.asyncio
async def test_a_kept_live_hint_that_contains_the_new_password_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],
) -> None:
    result, client = await _set(monkeypatch, live_hint=f"it is {PASSWORD.upper()}", typed=None)

    assert result.status == "failed"
    assert result.error_message == "twofa_hint_contains_password"
    assert PASSWORD not in str(result)
    # Refused before any SRP work and before anything was written.
    assert (stub_srp["check"], stub_srp["digest"]) == ([], [])
    assert _writes(client) == []


@pytest.mark.asyncio
async def test_a_kept_live_hint_without_the_password_is_still_kept(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - the write must be reached
) -> None:
    result, client = await _set(monkeypatch, live_hint="my first pet", typed=None)

    assert result.status == "ok"
    assert client.written().new_settings.hint == "my first pet"


@pytest.mark.asyncio
async def test_a_typed_hint_that_contains_the_password_is_refused_here_too(
    monkeypatch: pytest.MonkeyPatch,
    stub_srp: dict[str, list[Any]],  # noqa: ARG001 - not reached
) -> None:
    result, client = await _set(monkeypatch, live_hint=None, typed=f"{PASSWORD} really")

    assert result.error_message == "twofa_hint_contains_password"
    assert _writes(client) == []
