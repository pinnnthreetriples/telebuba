"""QR login endpoint tests — thin routes over mocked services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from schemas.qr_login import QrLoginView
from services.accounts import AccountActionError, QrLoginError
from tests.api.accounts_helpers import account
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_BASE = "/api/v1/accounts"
_URL = f"{_BASE}/qr-acc/qr-login"


def _view(status: str = "waiting") -> QrLoginView:
    return QrLoginView.model_validate(
        {
            "account_id": "qr-acc",
            "status": status,
            "qr_image": "data:image/svg+xml;base64,AAAA" if status == "waiting" else None,
        },
    )


@pytest.mark.asyncio
async def test_start_answers_the_placeholder_account(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str | None, str]] = []

    async def _start(label: str | None, session_device: str) -> object:
        seen.append((label, session_device))
        return account("qr-abc")

    monkeypatch.setattr("services.accounts.start_qr_login", _start)
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/start-qr-login",
            json={"label": "Seller", "session_device": "mobile"},
        )

    assert resp.status_code == 200
    assert resp.json()["account_id"] == "qr-abc"
    assert seen == [("Seller", "mobile")]


@pytest.mark.asyncio
async def test_start_defaults_to_the_desktop_device(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str | None, str]] = []

    async def _start(label: str | None, session_device: str) -> object:
        seen.append((label, session_device))
        return account("qr-abc")

    monkeypatch.setattr("services.accounts.start_qr_login", _start)
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/start-qr-login", json={})

    assert resp.status_code == 200
    assert seen == [(None, "desktop")]


@pytest.mark.asyncio
async def test_start_answers_a_refused_device_as_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _start(_label: str | None, _session_device: str) -> object:
        code = "mobile_api_not_configured"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.start_qr_login", _start)
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/start-qr-login", json={"session_device": "mobile"})

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "mobile_api_not_configured"


@pytest.mark.parametrize(
    ("method", "service", "kwargs"),
    [
        ("post", "begin_qr_login", {}),
        ("get", "get_qr_login", {}),
        ("post", "submit_qr_password", {"json": {"password": "pw"}}),
    ],
)
@pytest.mark.asyncio
async def test_each_route_answers_the_service_view(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    service: str,
    kwargs: dict[str, object],
) -> None:
    async def _fake(account_id: str, *_: object) -> QrLoginView:
        assert account_id == "qr-acc"
        return _view()

    monkeypatch.setattr(f"services.accounts.{service}", _fake)
    url = f"{_URL}/password" if service == "submit_qr_password" else _URL
    async with _client(app) as client:
        resp = await getattr(client, method)(url, **kwargs)

    assert resp.status_code == 200
    assert resp.json()["status"] == "waiting"
    assert resp.json()["qr_image"].startswith("data:image/svg+xml")


@pytest.mark.asyncio
async def test_the_password_reaches_the_service(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    async def _submit(account_id: str, password: str) -> QrLoginView:
        seen.append((account_id, password))
        return _view("alive")

    monkeypatch.setattr("services.accounts.submit_qr_password", _submit)
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/password", json={"password": "hunter2"})

    assert resp.status_code == 200
    assert seen == [("qr-acc", "hunter2")]
    assert "hunter2" not in resp.text


@pytest.mark.asyncio
async def test_an_empty_password_is_a_validation_error(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(f"{_URL}/password", json={"password": ""})

    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("method", "service", "path", "kwargs"),
    [
        ("post", "begin_qr_login", "", {}),
        ("get", "get_qr_login", "", {}),
        ("post", "submit_qr_password", "/password", {"json": {"password": "pw"}}),
    ],
)
@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("no_qr_login", 400),
        ("unknown_account", 400),
        ("not_awaiting_password", 400),
        ("already_signed_in", 400),
        ("account_exists", 409),
    ],
)
@pytest.mark.asyncio
async def test_a_refusal_maps_to_its_status_with_the_code(  # noqa: PLR0913 - table row
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    service: str,
    path: str,
    kwargs: dict[str, object],
    code: str,
    status: int,
) -> None:
    async def _refuse(*_: object) -> QrLoginView:
        raise QrLoginError(code)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(f"services.accounts.{service}", _refuse)
    async with _client(app) as client:
        resp = await getattr(client, method)(f"{_URL}{path}", **kwargs)

    assert resp.status_code == status
    assert resp.json()["error"]["message"] == code


@pytest.mark.asyncio
async def test_cancel_answers_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled: list[str] = []

    async def _cancel(account_id: str) -> None:
        cancelled.append(account_id)

    monkeypatch.setattr("services.accounts.cancel_qr_login", _cancel)
    async with _client(app) as client:
        resp = await client.delete(_URL)

    assert resp.status_code == 204
    assert cancelled == ["qr-acc"]


@pytest.mark.asyncio
async def test_a_path_id_with_a_separator_is_rejected(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get(f"{_BASE}/..%5Cevil/qr-login")

    assert resp.status_code in {404, 422}
