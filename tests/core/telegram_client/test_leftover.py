"""Ending a leftover session: ``True`` only on proof, the file kept otherwise, never a raise."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from telethon import errors
from telethon.tl.functions.auth import LogOutRequest

from core.config import settings
from core.telegram_client import _leftover
from core.telegram_client._client import DeviceUnavailableError
from core.telegram_client._leftover import end_leftover_session
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class _Leftover:
    """The client on the leftover file: ``outcome`` is what ``auth.logOut`` does."""

    def __init__(self, outcome: object = None) -> None:
        self.outcome = outcome
        self.requests: list[object] = []
        self.events: list[str] = []

    async def connect(self) -> None:
        self.events.append("connect")
        if isinstance(self.outcome, str) and self.outcome == "hang-connect":
            await asyncio.Event().wait()

    async def __call__(self, request: object) -> object:
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return True

    async def disconnect(self) -> None:
        self.events.append("disconnect")


@pytest.fixture
def leftover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    file = write_session_file(tmp_path / "sessions" / "acc_rekey.session")
    state: dict[str, object] = {}

    async def _prepare(request: object, **kwargs: object) -> object:
        state["request"] = request
        state["device"] = kwargs.get("device")
        return SimpleNamespace(session_path=str(file.with_suffix("")), account_id="acc-1")

    monkeypatch.setattr(_leftover, "prepare_telegram_client_profile", _prepare)

    def _arrange(client: _Leftover) -> Path:
        monkeypatch.setattr(_leftover, "create_telegram_client", lambda _profile: client)
        return file

    return _arrange


async def _end() -> bool:
    return await end_leftover_session("acc-1", "desktop", "acc_rekey")


@pytest.mark.asyncio
async def test_a_logout_that_answers_ends_it_and_the_file_goes_to_the_quarantine(
    leftover: Callable[..., Path],
) -> None:
    client = _Leftover()
    file = leftover(client)

    assert await _end() is True

    assert [type(r) for r in client.requests] == [LogOutRequest]
    assert not file.exists()
    assert list(settings.session_vault.quarantine_dir.glob("*acc_rekey.session"))
    assert client.events[-1] == "disconnect"


@pytest.mark.asyncio
async def test_a_key_telegram_says_is_dead_counts_as_ended(leftover: Callable[..., Path]) -> None:
    file = leftover(_Leftover(errors.AuthKeyUnregisteredError(request=None)))

    assert await _end() is True

    assert not file.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [ConnectionError("proxy down"), TimeoutError(), errors.FloodWaitError(request=None, capture=5)],
)
async def test_anything_unproven_keeps_the_file(
    leftover: Callable[..., Path], failure: BaseException
) -> None:
    file = leftover(_Leftover(failure))

    assert await _end() is False

    assert file.exists()


@pytest.mark.asyncio
async def test_a_hung_connect_is_bounded_and_keeps_the_file(
    leftover: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings.telegram, "session_check_timeout_seconds", 0.05)
    file = leftover(_Leftover("hang-connect"))

    assert await asyncio.wait_for(_end(), timeout=5) is False

    assert file.exists()


@pytest.mark.asyncio
async def test_a_profile_that_cannot_be_built_keeps_the_file(
    leftover: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    file = leftover(_Leftover())

    def _no_proxy(_profile: object) -> object:
        raise ConnectionError

    monkeypatch.setattr(_leftover, "create_telegram_client", _no_proxy)

    assert await _end() is False
    assert file.exists()

    async def _unavailable(_request: object, **_: object) -> object:
        code = "device_missing"
        raise DeviceUnavailableError(code, "acc-1")

    monkeypatch.setattr(_leftover, "prepare_telegram_client_profile", _unavailable)

    assert await _end() is False
    assert file.exists()


@pytest.mark.asyncio
async def test_the_session_is_opened_as_a_new_key_without_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[object, object]] = []
    device = object()

    async def _prepare(request: object, **kwargs: object) -> object:
        seen.append((request, kwargs.get("device")))
        return SimpleNamespace(session_path=str(tmp_path / "gone"), account_id="acc-1")

    monkeypatch.setattr(_leftover, "prepare_telegram_client_profile", _prepare)
    monkeypatch.setattr(_leftover, "create_telegram_client", lambda _profile: _Leftover())

    await end_leftover_session("acc-1", "mobile", "acc_mobile", device=device)  # ty: ignore[invalid-argument-type]

    ((request, given),) = seen
    assert request.model_dump() == {  # ty: ignore[unresolved-attribute]
        "account_id": "acc-1",
        "device_type": "mobile",
        "session_name": "acc_mobile",
        "receive_updates": False,
        "new_key": True,
    }
    assert given is device
