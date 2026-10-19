"""Contracts for the inbound SMS gateway, Flash Call extraction, and OTP typing sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

TelephonyLineStatus = Literal[
    "idle",
    "waiting_otp",
    "otp_received",
    "typing",
    "completed",
    "timeout",
]

TelephonyPlatform = Literal["telegram", "whatsapp", "facebook", "tiktok", "generic"]

PHONE_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InboundSmsWebhook(BaseModel):
    """Контракт входящего вебхука от шлюзов (GoIP, Dinstar), модемов или SMS-API."""

    phone: str = Field(
        min_length=1,
        description="Номер получателя в формате E.164, напр. +79991234567",
    )
    sender: str = Field(
        min_length=1,
        description="Имя отправителя или номер, напр. Telegram, WhatsApp, Facebook",
    )
    text: str = Field(
        default="",
        description="Сырой текст SMS-сообщения",
    )
    timestamp: datetime = Field(default_factory=_utc_now)


class OtpSession(BaseModel):
    """Активная сессия ожидания SMS для конкретного телефона."""

    phone_id: str = Field(pattern=PHONE_ID_PATTERN)
    phone_number: str = Field(min_length=1)
    platform: TelephonyPlatform = "telegram"
    status: TelephonyLineStatus = "waiting_otp"
    last_code: str | None = None
    last_sms_text: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime


class SmsWebhookResponse(BaseModel):
    """Ответ вебхука приёма входящих SMS."""

    status: Literal["ok"] = "ok"
    matched: bool
    code: str | None = None
    duplicate: bool = False


class PhoneTelephonyStatus(BaseModel):
    """Текущий статус сотовой линии и ожидания/ввода OTP для телефона."""

    phone_id: str = Field(pattern=PHONE_ID_PATTERN)
    phone_number: str | None = None
    platform: TelephonyPlatform | None = None
    status: TelephonyLineStatus = "idle"
    last_code: str | None = None
    last_sms_text: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    remaining_seconds: int = Field(default=0, ge=0)


class ManualOtpInputRequest(BaseModel):
    """Запрос на ручной биомеханический ввод OTP-кода оператором."""

    code: str = Field(min_length=4, max_length=8, pattern=r"^\d{4,8}$")


class ManualOtpInputResult(BaseModel):
    """Результат ручного ввода OTP-кода в контейнер смартфона."""

    status: Literal["ok"] = "ok"
    phone_id: str = Field(pattern=PHONE_ID_PATTERN)
    code: str
    line_status: TelephonyLineStatus = "completed"


class SmsInboxMessage(BaseModel):
    """Запись входящего SMS-сообщения в кольцевом журнале."""

    id: str
    phone: str
    phone_id: str | None = None
    sender: str
    text: str
    code: str | None = None
    platform: TelephonyPlatform = "generic"
    matched: bool = False
    timestamp: datetime = Field(default_factory=_utc_now)


class SmsInboxList(BaseModel):
    """Список последних входящих SMS для журнала (до 50 сообщений)."""

    messages: list[SmsInboxMessage]
