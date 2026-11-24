"""The pool never builds a client over a ``.session`` that is gone or holds no key.

Telethon creates an empty database at a missing path and writes a fresh, unregistered key into
it, so an account whose file was moved away read as a key Telegram revoked, and the junk file
then blocked restoring the real one. The refusal comes before Telethon's constructor; every
other state proceeds as it always did. Real SQLite files, no Telethon client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import TelegramClientPoolError, telegram_client
from core.telegram_client._pool import _reset_for_tests, get_client
from core.telegram_client._pool_errors import SessionFileMissingError
from schemas.device_fingerprint import TelegramClientRequest
from tests.session_files import write_session_file

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    _get_engine()
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    _reset_for_tests()
    yield
    _reset_for_tests()
    reset_logging_for_tests()


class _FakeClient:
    def __init__(self) -> None:
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_FakeClient]:
    """Every client the pool gets as far as building (Telethon's constructor stands here)."""
    clients: list[_FakeClient] = []

    def fake_create(_profile: object) -> _FakeClient:
        clients.append(_FakeClient())
        return clients[-1]

    monkeypatch.setattr("core.telegram_client._pool.create_telegram_client", fake_create)
    monkeypatch.setattr("core.telegram_client._client.create_telegram_client", fake_create)
    return clients


def _session_dir() -> Path:
    return settings.telegram.session_dir


@pytest.mark.asyncio
async def test_a_missing_session_file_is_refused_and_not_created(built: list[_FakeClient]) -> None:
    with pytest.raises(SessionFileMissingError) as raised:
        await get_client("gone")

    assert raised.value.state == "missing"
    assert raised.value.account_id == "gone"
    assert built == []  # Telethon's constructor never ran
    assert list(_session_dir().glob("*")) == []  # and nothing was created in its place


@pytest.mark.asyncio
async def test_the_refusal_is_an_unavailable_account_to_every_borrower(
    built: list[_FakeClient],
) -> None:
    with pytest.raises(TelegramClientPoolError):
        await get_client("gone")

    assert built == []


@pytest.mark.asyncio
async def test_a_session_that_never_held_a_key_is_refused(built: list[_FakeClient]) -> None:
    junk = write_session_file(_session_dir() / "junk.session", key=None)
    before = junk.read_bytes()

    with pytest.raises(SessionFileMissingError) as raised:
        await get_client("junk")

    assert raised.value.state == "no_key"
    assert built == []
    assert junk.read_bytes() == before


@pytest.mark.asyncio
async def test_the_refusal_is_not_retried_or_logged_as_a_connect_failure(
    built: list[_FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    async def record(_level: str, event: str, **_kwargs: object) -> None:
        events.append(event)

    monkeypatch.setattr("core.telegram_client._pool.log_event", record)

    with pytest.raises(SessionFileMissingError):
        await get_client("gone")

    assert events == []
    assert built == []


@pytest.mark.asyncio
async def test_a_session_with_a_key_is_served(built: list[_FakeClient]) -> None:
    write_session_file(_session_dir() / "fine.session")

    client = await get_client("fine")

    assert client is built[0]


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_read_right_now_proceeds_as_before(
    built: list[_FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "core.telegram_client._client.inspect_session_file", lambda _path: "unreadable"
    )

    await get_client("held")

    assert len(built) == 1


@pytest.mark.asyncio
async def test_an_empty_file_is_refused_and_left_as_it_is(built: list[_FakeClient]) -> None:
    # SQLite reads 0 bytes as an empty database: Telethon would create its tables in it.
    path = _session_dir() / "empty.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")

    with pytest.raises(SessionFileMissingError):
        await get_client("empty")

    assert built == []
    assert path.read_bytes() == b""


@pytest.mark.asyncio
async def test_the_context_manager_refuses_a_missing_file_too(built: list[_FakeClient]) -> None:
    with pytest.raises(SessionFileMissingError):
        async with telegram_client(TelegramClientRequest(account_id="gone")):
            pytest.fail("a client was handed out over a missing session file")

    assert built == []
    assert list(_session_dir().glob("*.session")) == []
