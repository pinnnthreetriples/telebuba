"""Schemas for the QR-code login flow (show QR -> phone scans it -> optional 2FA).

Pure data contracts between ``core.telegram_client._qr_login`` (the auth gateway),
``services.accounts.qr_login`` (orchestration) and ``api/v1/_accounts_qr_login``.
No behaviour, no I/O. The login token and its ``tg://`` URL never appear here: the
only thing that leaves the gateway is the rendered QR image.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic field type
from typing import Literal

from pydantic import BaseModel, Field

from schemas.accounts import AccountRead  # noqa: TC001 - Pydantic field type
from schemas.device_fingerprint import DeviceType  # noqa: TC001 - Pydantic field type
from schemas.telegram_session import TelegramSessionCheckResult  # noqa: TC001 - Pydantic field type

QrLoginStatus = Literal["waiting", "password_needed", "alive", "expired", "failed"]
QrLoginErrorCode = Literal[
    "unknown_account",
    "already_signed_in",
    "no_qr_login",
    "not_awaiting_password",
    "account_exists",
    "password_invalid",
    "flood_wait",
    "connection_failed",
]


class QrLoginRequest(BaseModel):
    """Gateway input: start a QR sign-in on an account's own session."""

    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)
    # Telegram user ids already signed in here; Telegram will not offer them the QR.
    ignored_user_ids: list[int] = Field(default_factory=list)


class QrLoginState(BaseModel):
    """Gateway output: where one QR sign-in stands right now."""

    account_id: str = Field(min_length=1)
    status: QrLoginStatus
    # SVG data URI of the current QR code; only while ``waiting``.
    qr_image: str | None = None
    # When the current QR token stops working; only while ``waiting``.
    expires_at: datetime | None = None
    # Set on ``failed`` and on a rejected 2FA password.
    error_code: QrLoginErrorCode | None = None
    flood_wait_seconds: int | None = None
    # The signed-in verdict; only when ``alive``. Never copied into the API view.
    result: TelegramSessionCheckResult | None = None


class QrLoginView(BaseModel):
    """API response: the QR sign-in's state for the SPA."""

    account_id: str
    status: QrLoginStatus
    qr_image: str | None = None
    expires_at: datetime | None = None
    error_code: QrLoginErrorCode | None = None
    flood_wait_seconds: int | None = None
    # The saved account; only when ``alive``.
    account: AccountRead | None = None


class StartQrLoginRequest(BaseModel):
    """API body for ``POST /accounts/start-qr-login`` — provision a placeholder account."""

    label: str | None = Field(default=None, min_length=1)
    # The device the login signs in as; mobile only by the operator's explicit pick.
    session_device: DeviceType = "desktop"


class SubmitQrPasswordRequest(BaseModel):
    """API body for ``POST /accounts/{id}/qr-login/password`` — the 2FA password."""

    password: str = Field(min_length=1)
