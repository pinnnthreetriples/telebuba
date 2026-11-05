"""Schemas for the phone-code login flow (request code → submit code → 2FA).

Pure data contracts between ``core.telegram_client._auth`` (the auth RPCs),
``services.accounts.login`` (orchestration + the in-memory TTL cache) and
``api/v1/accounts``. No behaviour, no I/O (non-negotiable #2). The
``phone_code_hash`` never leaves the backend — it is cached server-side and
the API surfaces only a confirmation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from schemas.device_fingerprint import DeviceType  # noqa: TC001 - Pydantic field type


class PhoneCodeRequest(BaseModel):
    """Gateway input: send a login code to an account's phone."""

    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)
    phone: str = Field(min_length=1)


class PhoneCodeChallenge(BaseModel):
    """Gateway output: the ``phone_code_hash`` tying a later submit to this request.

    ``phone_code_hash`` is empty and ``error`` is set when the request failed
    (the gateway classifies the Telethon failure so ``services`` need not import
    ``telethon`` to interpret it). ``error_code`` is the stable, locale-neutral
    classification (``flood_wait`` / ``unavailable`` / ``phone_invalid`` /
    ``phone_banned`` / ``failed``) and ``flood_wait_seconds`` the mandated wait.
    """

    account_id: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    phone_code_hash: str = ""
    error: str | None = None
    error_code: str | None = None
    flood_wait_seconds: int | None = Field(default=None, ge=0)


class PhoneCodeSubmit(BaseModel):
    """Gateway input: complete sign-in with the code (+ optional 2FA password)."""

    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)
    phone: str = Field(min_length=1)
    phone_code_hash: str = Field(min_length=1)
    code: str = Field(min_length=1)
    password: str | None = None
    # Tried in order after ``password`` when Telegram rejects it (or none was typed).
    fallback_passwords: list[str] = Field(default_factory=list)


class PhoneCodeRequestResult(BaseModel):
    """API response after a code is sent — confirmation only, no secrets."""

    account_id: str = Field(min_length=1)
    phone: str = Field(min_length=1)


class StartPhoneLoginRequest(BaseModel):
    """API body for ``POST /accounts/start-login`` — provision an account by phone."""

    phone: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)
    # The device the login signs in as; mobile only by the operator's explicit pick.
    session_device: DeviceType = "desktop"


class SubmitCodeRequest(BaseModel):
    """API body for ``POST /accounts/{id}/submit-code``."""

    code: str = Field(min_length=1)
    password: str | None = None
