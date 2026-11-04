"""Tests for the QR login service (gateway faked, real DB)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import configure_database, fetch_account, update_account_from_session_check
from schemas.accounts import AccountCreate
from schemas.qr_login import QrLoginRequest, QrLoginState
from schemas.telegram_session import TelegramSessionCheckResult
from services.accounts import AccountActionError, add_account, remove_account
from services.accounts import lifecycle as lifecycle_service
from services.accounts import login as login_service
from services.accounts import qr_login as qr_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from schemas.device_fingerprint import TelegramClientRequest
    from schemas.qr_login import QrLoginErrorCode


class Recorder:
    """Everything the faked collaborators were asked to do."""

    def __init__(self, session_path: str) -> None:
        self.session_path = session_path
        self.started: list[QrLoginRequest] = []
        self.submitted: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self.forgotten: list[str] = []
        self.logged_out: list[tuple[str, bool]] = []
        self.inboxes: list[str] = []
        self.persisted: list[str] = []
        self.events: list[str] = []
        self.state: QrLoginState | None = None
        self.start_state: QrLoginState | None = None
        self.submit_state: QrLoginState | None = None


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    qr_service._FINISHED.clear()
    qr_service._FINISH_LOCKS.clear()
    yield
    qr_service._FINISHED.clear()
    qr_service._FINISH_LOCKS.clear()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Recorder:
    rec = Recorder(str(tmp_path / "sessions" / "qr-placeholder"))

    async def _start(request: QrLoginRequest) -> QrLoginState:
        rec.started.append(request)
        return rec.start_state or QrLoginState(
            account_id=request.account_id,
            status="waiting",
            qr_image="data:image/svg+xml;base64,AAAA",
        )

    async def _submit(account_id: str, password: str) -> QrLoginState:
        rec.submitted.append((account_id, password))
        assert rec.submit_state is not None
        return rec.submit_state

    async def _cancel(account_id: str) -> None:
        rec.cancelled.append(account_id)

    async def _log_out(request: TelegramClientRequest, *, wipe_session: bool) -> object:
        rec.logged_out.append((request.account_id, wipe_session))
        return None

    async def _inbox(account_id: str) -> None:
        rec.inboxes.append(account_id)

    async def _persist(result: TelegramSessionCheckResult):
        rec.persisted.append(result.account_id)
        return await update_account_from_session_check(result)

    async def _event(level: str, event: str, **_: object) -> None:
        rec.events.append(f"{level}:{event}")

    monkeypatch.setattr(qr_service, "_gateway_start", _start)
    monkeypatch.setattr(qr_service, "_gateway_submit", _submit)
    monkeypatch.setattr(qr_service, "_gateway_cancel", _cancel)
    monkeypatch.setattr(qr_service, "read_qr_login", lambda _: rec.state)
    monkeypatch.setattr(qr_service, "forget_qr_login", rec.forgotten.append)
    monkeypatch.setattr(qr_service, "log_out_session", _log_out)
    monkeypatch.setattr(qr_service, "start_account_inbox", _inbox)
    monkeypatch.setattr(qr_service, "update_account_from_session_check", _persist)
    monkeypatch.setattr(qr_service, "log_event", _event)
    return rec


def _alive(account_id: str, rec: Recorder, user_id: int = 777) -> QrLoginState:
    return QrLoginState(
        account_id=account_id,
        status="alive",
        result=TelegramSessionCheckResult(
            account_id=account_id,
            session_path=rec.session_path,
            status="alive",
            is_temporary=False,
            user_id=user_id,
            phone="79990001122",
            username="scanned",
        ),
    )


async def _placeholder(account_id: str = "qr-1") -> None:
    await add_account(AccountCreate(account_id=account_id, session_name=account_id))


async def _existing_user(account_id: str, user_id: int) -> None:
    await add_account(AccountCreate(account_id=account_id))
    await update_account_from_session_check(
        TelegramSessionCheckResult(
            account_id=account_id,
            session_path="x",
            status="alive",
            is_temporary=False,
            user_id=user_id,
        ),
    )


@pytest.mark.asyncio
async def test_start_creates_a_placeholder_without_connecting(fake: Recorder) -> None:
    account = await qr_service.start_qr_login(label="Seller", session_device="desktop")

    assert account.account_id.startswith("qr-")
    assert account.session_name == account.account_id
    assert account.phone is None
    assert account.label == "Seller"
    assert account.status == "new"
    assert fake.started == []
    assert "INFO:qr_login_started" in fake.events
    assert await fetch_account(account.account_id) is not None


@pytest.mark.asyncio
async def test_start_ids_are_unique(fake: Recorder) -> None:
    first = await qr_service.start_qr_login()
    second = await qr_service.start_qr_login()

    assert first.account_id != second.account_id
    assert fake.started == []


@pytest.mark.asyncio
async def test_start_refuses_mobile_when_it_is_not_configured(
    fake: Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("services.accounts.devices.mobile_api_configured", lambda: False)

    with pytest.raises(AccountActionError):
        await qr_service.start_qr_login(session_device="mobile")

    assert fake.started == []


@pytest.mark.asyncio
async def test_begin_unknown_account_is_refused(fake: Recorder) -> None:
    with pytest.raises(qr_service.QrLoginError) as excinfo:
        await qr_service.begin_qr_login("nope")

    assert excinfo.value.code == "unknown_account"
    assert fake.started == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake")
async def test_begin_on_a_signed_in_account_is_refused() -> None:
    await _existing_user("acc", 5)

    with pytest.raises(qr_service.QrLoginError) as excinfo:
        await qr_service.begin_qr_login("acc")

    assert excinfo.value.code == "already_signed_in"


@pytest.mark.asyncio
async def test_begin_checks_the_device_is_configured(
    fake: Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await add_account(AccountCreate(account_id="qr-m", session_device="mobile"))
    monkeypatch.setattr("services.accounts.devices.mobile_api_configured", lambda: False)

    with pytest.raises(AccountActionError):
        await qr_service.begin_qr_login("qr-m")

    assert fake.started == []


@pytest.mark.asyncio
async def test_begin_passes_the_other_accounts_user_ids(fake: Recorder) -> None:
    await _existing_user("one", 11)
    await _existing_user("two", 22)
    await add_account(AccountCreate(account_id="no-user"))
    await _placeholder()

    view = await qr_service.begin_qr_login("qr-1")

    assert [(r.account_id, r.session_name) for r in fake.started] == [("qr-1", "qr-1")]
    assert sorted(fake.started[0].ignored_user_ids) == [11, 22]
    assert view.status == "waiting"
    assert view.qr_image == "data:image/svg+xml;base64,AAAA"
    assert view.account is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake")
async def test_poll_without_a_sign_in_is_refused() -> None:
    with pytest.raises(qr_service.QrLoginError) as excinfo:
        await qr_service.get_qr_login("qr-1")

    assert excinfo.value.code == "no_qr_login"


@pytest.mark.asyncio
async def test_poll_reports_the_waiting_state(fake: Recorder) -> None:
    fake.state = QrLoginState(account_id="qr-1", status="password_needed")

    view = await qr_service.get_qr_login("qr-1")

    assert view.status == "password_needed"
    assert view.account is None


@pytest.mark.asyncio
async def test_poll_carries_a_failure_code(fake: Recorder) -> None:
    fake.state = QrLoginState(
        account_id="qr-1",
        status="failed",
        error_code="flood_wait",
        flood_wait_seconds=30,
    )

    view = await qr_service.get_qr_login("qr-1")

    assert (view.status, view.error_code, view.flood_wait_seconds) == ("failed", "flood_wait", 30)


@pytest.mark.asyncio
async def test_alive_is_persisted_once_however_often_it_is_polled(fake: Recorder) -> None:
    await _placeholder()
    fake.state = _alive("qr-1", fake)

    first = await qr_service.get_qr_login("qr-1")
    fake.state = None  # the gateway entry was forgotten after the first poll
    second = await qr_service.get_qr_login("qr-1")

    assert first.status == second.status == "alive"
    assert first.account is not None
    assert first.account.status == "alive"
    assert first.account.user_id == 777
    assert first.account.phone == "79990001122"
    assert second.account == first.account
    assert fake.persisted == ["qr-1"]
    assert fake.inboxes == ["qr-1"]
    assert fake.forgotten == ["qr-1"]
    assert "INFO:qr_login_success" in fake.events


@pytest.mark.asyncio
async def test_two_polls_while_still_alive_persist_once(fake: Recorder) -> None:
    await _placeholder()
    fake.state = _alive("qr-1", fake)

    first = await qr_service.get_qr_login("qr-1")
    second = await qr_service.get_qr_login("qr-1")

    assert second.account == first.account
    assert fake.persisted == ["qr-1"]


@pytest.mark.asyncio
async def test_a_finished_sign_in_of_a_removed_account_is_not_reported(fake: Recorder) -> None:
    from core.db import delete_account  # noqa: PLC0415

    await _placeholder()
    fake.state = _alive("qr-1", fake)
    await qr_service.get_qr_login("qr-1")
    fake.state = None
    await delete_account("qr-1")

    with pytest.raises(qr_service.QrLoginError) as excinfo:
        await qr_service.get_qr_login("qr-1")

    assert excinfo.value.code == "no_qr_login"
    assert "qr-1" not in qr_service._FINISHED


@pytest.mark.asyncio
async def test_the_view_never_carries_the_session_verdict(fake: Recorder) -> None:
    await _placeholder()
    fake.state = _alive("qr-1", fake)

    view = await qr_service.get_qr_login("qr-1")

    assert "result" not in view.model_dump()
    assert "session_path" not in view.model_dump_json()


@pytest.mark.asyncio
async def test_begin_on_an_already_authorized_session_finishes_at_once(fake: Recorder) -> None:
    await _placeholder()
    fake.start_state = _alive("qr-1", fake)

    view = await qr_service.begin_qr_login("qr-1")

    assert view.status == "alive"
    assert view.account is not None
    assert fake.persisted == ["qr-1"]


@pytest.mark.asyncio
async def test_a_user_telebuba_already_has_is_revoked_and_dropped(fake: Recorder) -> None:
    await _existing_user("known", 777)
    await _placeholder()
    fake.state = _alive("qr-1", fake, user_id=777)

    with pytest.raises(qr_service.QrLoginError) as excinfo:
        await qr_service.get_qr_login("qr-1")

    assert excinfo.value.code == "account_exists"
    assert fake.logged_out == [("qr-1", True)]
    assert await fetch_account("qr-1") is None
    assert await fetch_account("known") is not None
    assert fake.persisted == []
    assert fake.inboxes == []
    assert fake.forgotten == ["qr-1"]
    assert "INFO:qr_login_duplicate" in fake.events


@pytest.mark.asyncio
async def test_a_failed_revoke_still_drops_the_placeholder(
    fake: Recorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(request: TelegramClientRequest, *, wipe_session: bool) -> object:
        raise ConnectionError(request.account_id, wipe_session)

    monkeypatch.setattr(qr_service, "log_out_session", _boom)
    await _existing_user("known", 777)
    await _placeholder()
    fake.state = _alive("qr-1", fake, user_id=777)

    with pytest.raises(qr_service.QrLoginError):
        await qr_service.get_qr_login("qr-1")

    assert await fetch_account("qr-1") is None
    assert "WARNING:qr_login_duplicate_logout_failed" in fake.events


@pytest.mark.asyncio
async def test_a_signed_in_user_without_an_id_is_not_a_duplicate(fake: Recorder) -> None:
    await _placeholder()
    state = _alive("qr-1", fake)
    assert state.result is not None
    state.result.user_id = None
    fake.state = state

    view = await qr_service.get_qr_login("qr-1")

    assert view.account is not None


@pytest.mark.asyncio
async def test_submit_password_returns_the_next_state(fake: Recorder) -> None:
    fake.submit_state = QrLoginState(
        account_id="qr-1",
        status="password_needed",
        error_code="password_invalid",
    )

    view = await qr_service.submit_qr_password("qr-1", "hunter2")

    assert fake.submitted == [("qr-1", "hunter2")]
    assert view.error_code == "password_invalid"
    assert "hunter2" not in view.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake")
async def test_submit_password_maps_the_gateway_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refuse(_account_id: str, _password: str) -> QrLoginState:
        code: QrLoginErrorCode = "not_awaiting_password"
        raise qr_service.QrLoginGatewayError(code)

    monkeypatch.setattr(qr_service, "_gateway_submit", _refuse)

    with pytest.raises(qr_service.QrLoginError) as excinfo:
        await qr_service.submit_qr_password("qr-1", "pw")

    assert excinfo.value.code == "not_awaiting_password"


@pytest.mark.asyncio
async def test_cancel_stops_the_gateway_and_forgets_the_outcome(fake: Recorder) -> None:
    qr_service._FINISHED["qr-1"] = None  # ty: ignore[invalid-assignment]

    await qr_service.cancel_qr_login("qr-1")

    assert fake.cancelled == ["qr-1"]
    assert "qr-1" not in qr_service._FINISHED


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake")
async def test_remove_account_cancels_an_open_qr_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled: list[str] = []

    async def _cancel(account_id: str) -> None:
        cancelled.append(account_id)

    monkeypatch.setattr(lifecycle_service, "cancel_qr_login", _cancel)
    await _placeholder()

    await remove_account("qr-1")

    assert cancelled == ["qr-1"]
    assert await fetch_account("qr-1") is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("fake")
async def test_logout_cancels_an_open_qr_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled: list[str] = []

    async def _cancel(account_id: str) -> None:
        cancelled.append(account_id)

    async def _logout(request: TelegramClientRequest, *, wipe_session: bool) -> object:  # noqa: ARG001
        return TelegramSessionCheckResult(
            account_id=request.account_id,
            session_path="x",
            status="unauthorized",
            is_temporary=False,
        )

    monkeypatch.setattr(login_service, "cancel_qr_login", _cancel)
    monkeypatch.setattr(login_service, "log_out_session", _logout)
    await _placeholder()

    await login_service.logout_account("qr-1")

    assert cancelled == ["qr-1"]
