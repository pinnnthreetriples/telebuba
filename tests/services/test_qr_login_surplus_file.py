"""The surplus QR authorization's ``.session`` on a REAL disk: set aside, never unlinked."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core import session_vault
from core.config import settings
from core.db import configure_database, fetch_account, update_account_from_session_check
from core.telegram_client import _pool
from schemas.accounts import AccountCreate
from schemas.qr_login import QrLoginState
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import add_account
from services.accounts import qr_login as qr_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.device_fingerprint import TelegramClientRequest

_KEY_BYTES = b"SQLite format 3\x00the only copy of a live auth key"


@pytest.fixture
def disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    configure_database(tmp_path / "telebuba.db")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    quarantine = tmp_path / "quarantine"
    monkeypatch.setattr(settings.telegram, "session_dir", sessions)
    monkeypatch.setattr(settings.session_vault, "quarantine_dir", quarantine)
    qr_service._FINISHED.clear()
    qr_service._FINISH_LOCKS.clear()
    yield {"sessions": sessions, "quarantine": quarantine}
    qr_service._FINISHED.clear()
    qr_service._FINISH_LOCKS.clear()


class _Pooled:
    """A pooled client holding the placeholder's file; records when it is let go."""

    def __init__(self, order: list[str]) -> None:
        self._order = order

    async def disconnect(self) -> None:
        self._order.append("evicted")


async def _scene(disk: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    await add_account(AccountCreate(account_id="known"))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id="known", session_path="x", status="alive", is_temporary=False, user_id=777
        )
    )
    await add_account(AccountCreate(account_id="qr-1", session_name="qr-1"))
    file = disk["sessions"] / "qr-1.session"
    file.write_bytes(_KEY_BYTES)
    state = QrLoginState(
        account_id="qr-1",
        status="alive",
        result=TelegramSessionCheckResult(
            account_id="qr-1",
            session_path=str(disk["sessions"] / "qr-1"),
            status="alive",
            is_temporary=False,
            user_id=777,
        ),
    )
    monkeypatch.setattr(qr_service, "read_qr_login", lambda _: state)
    monkeypatch.setattr(qr_service, "forget_qr_login", lambda _: None)
    return file


def _answer(
    monkeypatch: pytest.MonkeyPatch, *, status: str | None, order: list[str] | None = None
) -> None:
    async def _log_out(request: TelegramClientRequest, *, wipe_session: bool) -> object:
        assert wipe_session
        if status is None:
            msg = "proxy dropped"
            raise ConnectionError(msg)
        return TelegramSessionCheckResult(
            account_id=request.account_id,
            session_path="x",
            status=status,  # ty: ignore[invalid-argument-type]
            is_temporary=status == "network_error",
            error_type="TimeoutError" if status == "network_error" else None,
        )

    monkeypatch.setattr(qr_service, "log_out_session", _log_out)
    if order is not None:
        real_quarantine = session_vault.quarantine_file

        def _moved(path: Path, **kw: object) -> Path | None:
            order.append("moved")
            return real_quarantine(path, **kw)  # ty: ignore[invalid-argument-type]

        monkeypatch.setattr("core.telegram_client._auth.quarantine_file", _moved)


@pytest.mark.parametrize("status", ["network_error", "session_error", "alive", None])
@pytest.mark.asyncio
async def test_an_unconfirmed_logout_keeps_the_key_bytes_in_the_quarantine(
    disk: dict[str, Path], monkeypatch: pytest.MonkeyPatch, status: str | None
) -> None:
    file = await _scene(disk, monkeypatch)
    order: list[str] = []
    _answer(monkeypatch, status=status, order=order)
    _pool._CLIENTS[("qr-1", "desktop")] = _Pooled(order)  # ty: ignore[invalid-assignment]
    try:
        with pytest.raises(qr_service.QrLoginError) as refused:
            await qr_service.get_qr_login("qr-1")
    finally:
        _pool._CLIENTS.pop(("qr-1", "desktop"), None)

    assert refused.value.code == "account_exists"
    assert not file.exists()
    kept = list(disk["quarantine"].iterdir())
    assert [p.read_bytes() for p in kept] == [_KEY_BYTES]
    assert kept[0].name.endswith("-qr-1.session")
    assert await fetch_account("qr-1") is None
    # Windows: the pooled client lets go of the file BEFORE it is moved.
    assert order.index("evicted") < order.index("moved")


@pytest.mark.asyncio
async def test_a_confirmed_logout_leaves_the_file_to_the_old_path(
    disk: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The (faked) gateway confirmed the logout but did not move the file as the real one
    # does; the rollback then sets it aside itself. Nothing is ever unlinked.
    file = await _scene(disk, monkeypatch)
    _answer(monkeypatch, status="unauthorized")

    with pytest.raises(qr_service.QrLoginError):
        await qr_service.get_qr_login("qr-1")

    assert [entry.read_bytes() for entry in disk["quarantine"].iterdir()] == [_KEY_BYTES]
    assert not file.exists()  # the placeholder's file is out of the sessions folder
    assert await fetch_account("qr-1") is None


@pytest.mark.asyncio
async def test_a_quarantine_that_cannot_be_written_still_refuses_as_account_exists(
    disk: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    file = await _scene(disk, monkeypatch)
    _answer(monkeypatch, status="network_error")

    def _full(_path: object, **_kw: object) -> None:
        raise OSError

    monkeypatch.setattr("core.telegram_client._auth.quarantine_file", _full)

    with pytest.raises(qr_service.QrLoginError) as refused:
        await qr_service.get_qr_login("qr-1")

    assert refused.value.code == "account_exists"
    assert file.read_bytes() == _KEY_BYTES  # whatever else happens, the key is not destroyed
