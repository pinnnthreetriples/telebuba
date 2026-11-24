"""The optional daily raft snapshot of the key vault."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest

from core import passkey_vault_snapshot as snap
from core.config import settings
from services import session_backups

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_real_client = httpx.AsyncClient


def _serve(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    def _factory(*_a: object, **_kwargs: object) -> httpx.AsyncClient:
        return _real_client(transport=httpx.MockTransport(_handle), timeout=10.0)

    monkeypatch.setattr(snap.httpx, "AsyncClient", _factory)
    return seen


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "backups")
    monkeypatch.setattr(settings.passkey_vault, "snapshot_token", "snap-token")
    monkeypatch.setattr(settings.session_vault, "mirror_dir", tmp_path / "mirror")
    session_backups.reset_for_tests()
    yield
    session_backups.reset_for_tests()


@pytest.mark.asyncio
async def test_a_snapshot_is_streamed_published_mirrored_and_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, content=b"raft-bytes"))
    base = datetime(2026, 11, 20, tzinfo=UTC)
    for day in range(9):
        await snap.take_vault_snapshot(now=base + timedelta(days=day))

    request = seen[0]
    assert request.url.path == "/v1/sys/storage/raft/snapshot"
    assert request.headers["X-Vault-Token"] == "snap-token"
    local = sorted((tmp_path / "backups").glob("openbao-*.snap"))
    assert len(local) == snap.KEEP
    assert local[-1].read_bytes() == b"raft-bytes"
    assert len(list((tmp_path / "mirror" / "openbao").glob("*.snap"))) == snap.KEEP
    assert not list((tmp_path / "backups").glob("*.part"))


@pytest.mark.asyncio
async def test_a_refusal_leaves_no_file_and_is_logged_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(monkeypatch, lambda _r: httpx.Response(403, content=b"secret-body"))
    logged: list[tuple[str, object]] = []

    async def _log(_level: str, event: str, **kwargs: object) -> None:
        logged.append((event, kwargs.get("extra")))

    monkeypatch.setattr(session_backups, "log_event", _log)

    await session_backups._snapshot_key_vault()

    assert not list((tmp_path / "backups").glob("openbao-*"))
    assert logged == [
        (
            "passkey_vault_snapshot_failed",
            {"error_type": "passkey_vault_rejected", "status": "http_403"},
        )
    ]
    assert "secret-body" not in str(logged)
    # Not hammered again before the retry delay.
    await session_backups._snapshot_key_vault()
    assert len(logged) == 1


@pytest.mark.asyncio
async def test_a_fresh_snapshot_is_not_repeated_and_no_token_means_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _serve(monkeypatch, lambda _r: httpx.Response(200, content=b"x"))

    await session_backups._snapshot_key_vault()
    await session_backups._snapshot_key_vault()
    assert len(seen) == 1

    monkeypatch.setattr(settings.passkey_vault, "snapshot_token", "")
    monkeypatch.setattr(settings.db, "backup_dir", tmp_path / "other")
    await session_backups._snapshot_key_vault()
    assert len(seen) == 1
