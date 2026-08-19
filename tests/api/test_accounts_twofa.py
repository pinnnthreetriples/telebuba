"""Cloud-password endpoint tests — thin routes over mocked 2FA services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from api import create_app
from schemas.telegram_actions_twofa import TwoFactorStatusResult
from schemas.twofa import (
    AccountTwoFactorCreated,
    AccountTwoFactorEmailConfirmRequest,
    AccountTwoFactorEmailPending,
    AccountTwoFactorEmailRequest,
    AccountTwoFactorUpdateRequest,
    AccountTwoFactorView,
)
from services.accounts import AccountActionError, AccountNotFoundError
from tests.api.accounts_helpers import client as _client

if TYPE_CHECKING:
    from fastapi import FastAPI

_TWOFA_URL = "/api/v1/accounts/acc-1/2fa"


@pytest.mark.asyncio
async def test_get_account_twofa_returns_the_live_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountTwoFactorView:
        assert account_id == "acc-1"
        return AccountTwoFactorView(
            status=TwoFactorStatusResult(
                has_password=True,
                hint="the usual",
                has_recovery=True,
                pending_reset_date="2026-03-01T12:00:00+00:00",
                email_unconfirmed_pattern="r**@example.com",
            ),
            has_stored_password=True,
        )

    monkeypatch.setattr("services.accounts.read_account_twofa", _fake)
    async with _client(app) as client:
        resp = await client.get(_TWOFA_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == {
        "has_password": True,
        "hint": "the usual",
        "has_recovery": True,
        "pending_reset_date": "2026-03-01T12:00:00+00:00",
        "email_unconfirmed_pattern": "r**@example.com",
    }
    assert body["has_stored_password"] is True
    assert body["error"] is None
    # The read surface carries booleans and the public hint — never the password.
    assert "password" not in body


@pytest.mark.asyncio
async def test_get_account_twofa_surfaces_a_refused_read_as_the_error_envelope(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountTwoFactorView:  # noqa: ARG001
        return AccountTwoFactorView(error="FloodWait(30s)", has_stored_password=True)

    monkeypatch.setattr("services.accounts.read_account_twofa", _fake)
    async with _client(app) as client:
        resp = await client.get(_TWOFA_URL)

    assert resp.status_code == 200
    assert resp.json() == {
        "status": None,
        "has_stored_password": True,
        "error": "FloodWait(30s)",
    }


@pytest.mark.asyncio
async def test_set_account_twofa_returns_the_password_once(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[AccountTwoFactorUpdateRequest] = []

    async def _fake(
        account_id: str,
        body: AccountTwoFactorUpdateRequest,
    ) -> AccountTwoFactorCreated:
        assert account_id == "acc-1"
        seen.append(body)
        return AccountTwoFactorCreated(password="generated-one", hint=body.hint)

    monkeypatch.setattr("services.accounts.set_account_twofa", _fake)
    async with _client(app) as client:
        resp = await client.post(_TWOFA_URL, json={"hint": "mine"})

    assert resp.status_code == 200
    assert resp.json() == {
        "password": "generated-one",
        "hint": "mine",
        "stored": True,
        "confirmed": True,
    }
    assert [(b.password, b.hint) for b in seen] == [(None, "mine")]
    # The one response in this API carrying a plaintext credential.
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "call", "code"),
    [
        pytest.param(
            "set_account_twofa",
            ("POST", _TWOFA_URL, {}),
            "twofa_password_not_stored",
            id="set",
        ),
        pytest.param(
            "remove_account_twofa",
            ("DELETE", _TWOFA_URL, None),
            "twofa_password_not_stored",
            id="remove",
        ),
        pytest.param(
            "set_account_twofa_email",
            ("POST", f"{_TWOFA_URL}/email", {"email": "a@b.co"}),
            "twofa_password_not_stored",
            id="set-email",
        ),
        pytest.param(
            "confirm_account_twofa_email",
            ("POST", f"{_TWOFA_URL}/email/confirm", {"code": "000000"}),
            "twofa_email_code_invalid",
            id="confirm-email",
        ),
        pytest.param(
            "clear_account_twofa_email",
            ("DELETE", f"{_TWOFA_URL}/email/recovery", None),
            "twofa_password_not_stored",
            id="clear-email",
        ),
    ],
)
async def test_a_service_refusal_becomes_a_400_carrying_its_stable_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    call: tuple[str, str, dict[str, str] | None],
    code: str,
) -> None:
    method, url, json_body = call

    async def _refuse(*_args: object) -> object:
        raise AccountActionError(code)

    monkeypatch.setattr(f"services.accounts.{service}", _refuse)
    async with _client(app) as client:
        resp = await client.request(method, url, json=json_body)

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url_suffix", "json_body"),
    [
        # ``extra="forbid"``: a typo'd key must 422, not silently generate a password.
        pytest.param("", {"passwrod": "abcdefgh"}, id="unknown-field"),
        # Eight characters is Telegram's own floor.
        pytest.param("", {"password": "short"}, id="short-password"),
        # A weak address check on purpose — Telegram is the real validator.
        pytest.param("/email", {"email": "not-an-address"}, id="not-an-address"),
        # 254 is RFC 5321's ceiling; one over must not reach the wire.
        pytest.param("/email", {"email": f"{'a' * 250}@b.co"}, id="over-long-address"),
        pytest.param("/email", {"email": "a@b.co", "current_password": "leak-me"}, id="extra-key"),
        pytest.param("/email/confirm", {"code": ""}, id="empty-code"),
    ],
)
async def test_a_constraint_violation_is_a_422_before_any_service_call(
    app: FastAPI,
    url_suffix: str,
    json_body: dict[str, str],
) -> None:
    """No service is patched here: a 422 proves the body never reached one."""
    async with _client(app) as client:
        resp = await client.post(f"{_TWOFA_URL}{url_suffix}", json=json_body)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_set_account_twofa_rejects_a_hint_containing_the_password(app: FastAPI) -> None:
    """The hint is shown at the login prompt to anyone holding the phone number."""
    async with _client(app) as client:
        resp = await client.post(
            _TWOFA_URL,
            json={"password": "Correct-Horse", "hint": "it is correct-horse"},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    # The refusal must not echo either value back.
    assert "Correct-Horse" not in str(body)


@pytest.mark.asyncio
async def test_remove_account_twofa_answers_with_the_re_read_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountTwoFactorView:
        assert account_id == "acc-1"
        return AccountTwoFactorView(status=TwoFactorStatusResult())

    monkeypatch.setattr("services.accounts.remove_account_twofa", _fake)
    async with _client(app) as client:
        resp = await client.delete(_TWOFA_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["has_password"] is False
    assert body["has_stored_password"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "json_body"),
    [
        ("GET", _TWOFA_URL, None),
        ("POST", _TWOFA_URL, {}),
        ("DELETE", _TWOFA_URL, None),
        ("POST", f"{_TWOFA_URL}/email", {"email": "a@b.co"}),
        ("POST", f"{_TWOFA_URL}/email/confirm", {"code": "424242"}),
        ("POST", f"{_TWOFA_URL}/email/resend", None),
        ("DELETE", f"{_TWOFA_URL}/email", None),
        ("DELETE", f"{_TWOFA_URL}/email/recovery", None),
    ],
)
async def test_twofa_routes_require_authentication(
    method: str,
    url: str,
    json_body: dict[str, str] | None,
) -> None:
    """Raw app (no ``get_current_user`` override) — the auth gate is real here."""
    async with _client(create_app()) as client:
        resp = await client.request(method, url, json=json_body)

    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Recovery-email routes. Two steps by design: attaching the address and typing
# the mailed code back are separate requests, so a failure in the second cannot
# cost the operator the password the first ``POST /2fa`` handed them once.
# --------------------------------------------------------------------------- #
_EMAIL_URL = f"{_TWOFA_URL}/email"


@pytest.mark.asyncio
async def test_set_account_twofa_email_reports_the_pending_code_length(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def _fake(
        account_id: str,
        body: AccountTwoFactorEmailRequest,
    ) -> AccountTwoFactorEmailPending:
        assert account_id == "acc-1"
        seen.append(body.email)
        return AccountTwoFactorEmailPending(pending=True, code_length=6)

    monkeypatch.setattr("services.accounts.set_account_twofa_email", _fake)
    async with _client(app) as client:
        resp = await client.post(_EMAIL_URL, json={"email": "recovery@example.com"})

    assert resp.status_code == 200
    assert resp.json() == {"pending": True, "code_length": 6}
    assert seen == ["recovery@example.com"]


@pytest.mark.asyncio
async def test_confirm_account_twofa_email_answers_with_the_fresh_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def _fake(
        account_id: str,
        body: AccountTwoFactorEmailConfirmRequest,
    ) -> AccountTwoFactorView:
        assert account_id == "acc-1"
        seen.append(body.code)
        return AccountTwoFactorView(
            status=TwoFactorStatusResult(has_password=True, has_recovery=True),
            has_stored_password=True,
        )

    monkeypatch.setattr("services.accounts.confirm_account_twofa_email", _fake)
    async with _client(app) as client:
        resp = await client.post(f"{_EMAIL_URL}/confirm", json={"code": "424242"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["has_recovery"] is True
    assert seen == ["424242"]
    assert "424242" not in str(body)


@pytest.mark.asyncio
async def test_clear_account_twofa_email_answers_with_the_re_read_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its own route, not a mode on the cancel one.

    Detaching a CONFIRMED address is a different Telegram call from cancelling a
    pending verification, so it cannot share ``DELETE .../email``.
    """

    async def _fake(account_id: str) -> AccountTwoFactorView:
        assert account_id == "acc-1"
        return AccountTwoFactorView(
            status=TwoFactorStatusResult(has_password=True, has_recovery=False),
            has_stored_password=True,
        )

    monkeypatch.setattr("services.accounts.clear_account_twofa_email", _fake)
    async with _client(app) as client:
        resp = await client.delete(f"{_EMAIL_URL}/recovery")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["has_recovery"] is False
    # The cloud password is untouched, so our copy of it stays.
    assert body["has_stored_password"] is True


@pytest.mark.asyncio
async def test_resend_account_twofa_email_takes_no_body(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountTwoFactorEmailPending:
        assert account_id == "acc-1"
        return AccountTwoFactorEmailPending(pending=True)

    monkeypatch.setattr("services.accounts.resend_account_twofa_email", _fake)
    async with _client(app) as client:
        resp = await client.post(f"{_EMAIL_URL}/resend")

    assert resp.status_code == 200
    assert resp.json() == {"pending": True, "code_length": None}


@pytest.mark.asyncio
async def test_cancel_account_twofa_email_answers_with_the_fresh_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> AccountTwoFactorView:
        assert account_id == "acc-1"
        return AccountTwoFactorView(
            status=TwoFactorStatusResult(has_password=True),
            has_stored_password=True,
        )

    monkeypatch.setattr("services.accounts.cancel_account_twofa_email", _fake)
    async with _client(app) as client:
        resp = await client.delete(_EMAIL_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"]["has_recovery"] is False
    assert body["has_stored_password"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service", "method", "path"),
    [
        pytest.param("read_account_twofa", "GET", "", id="get"),
        pytest.param("cancel_account_twofa_email", "DELETE", "/email", id="cancel-email"),
    ],
)
async def test_an_unknown_account_is_404_on_every_route_that_reads_one(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    method: str,
    path: str,
) -> None:
    """``AccountNotFoundError`` is a ``LookupError``, so it must not collapse to 400."""

    async def _missing(account_id: str) -> AccountTwoFactorView:
        raise AccountNotFoundError(account_id)

    monkeypatch.setattr(f"services.accounts.{service}", _missing)
    async with _client(app) as client:
        resp = await client.request(method, f"/api/v1/accounts/nope/2fa{path}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
