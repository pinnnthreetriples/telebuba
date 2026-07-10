"""Schemas for the phone-code login flow (request code → submit code → 2FA).

Pure data contracts between ``core.telegram_client._auth`` (the auth RPCs),
``services.accounts.login`` (orchestration + the in-memory TTL cache) and
``api/v1/accounts``. No behaviour, no I/O (non-negotiable #2). The
``phone_code_hash`` never leaves the backend — it is cached server-side and
the API surfaces only a confirmation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PhoneCodeRequest(BaseModel):
    """Gateway input: send a login code to an account's phone."""

    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)
    phone: str = Field(min_length=1)


class PhoneCodeChallenge(BaseModel):
    """Gateway output: the ``phone_code_hash`` tying a later submit to this request.

    ``phone_code_hash`` is empty and ``error`` is set when the request failed
    (the gateway classifies the Telethon failure so ``services`` need not import
    ``telethon`` to interpret it).
    """

    account_id: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    phone_code_hash: str = ""
    error: str | None = None


class PhoneCodeSubmit(BaseModel):
    """Gateway input: complete sign-in with the code (+ optional 2FA password)."""

    account_id: str = Field(min_length=1)
    session_name: str | None = Field(default=None, min_length=1)
    phone: str = Field(min_length=1)
    phone_code_hash: str = Field(min_length=1)
    code: str = Field(min_length=1)
    password: str | None = None


class PhoneCodeRequestResult(BaseModel):
    """API response after a code is sent — confirmation only, no secrets."""

    account_id: str = Field(min_length=1)
    phone: str = Field(min_length=1)


class StartPhoneLoginRequest(BaseModel):
    """API body for ``POST /accounts/start-login`` — provision an account by phone."""

    phone: str = Field(min_length=1)
    label: str | None = Field(default=None, min_length=1)


class SubmitCodeRequest(BaseModel):
    """API body for ``POST /accounts/{id}/submit-code``."""

    code: str = Field(min_length=1)
    password: str | None = None
