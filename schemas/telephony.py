"""Contracts for the inbound SMS gateway, Flash Call extraction, and OTP typing sessions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

TelephonyLineStatus = Literal[
    "idle",
    "waiting_otp",
    "otp_received",
    "typing",
    "completed",
    "failed",
    "timeout",
]

TelephonyPlatform = Literal["telegram", "whatsapp", "facebook", "tiktok", "generic"]

# ``sms``: a text to parse. ``flash_call``: a missed call whose caller id ends in the code.
InboundKind = Literal["sms", "flash_call"]

PHONE_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,128}$"
# ASCII digits only: ``\d`` would let Arabic-Indic or other Unicode digits through,
# which ``adb input text`` cannot type.
OTP_CODE_PATTERN = r"^[0-9]{4,8}$"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class InboundSmsWebhook(BaseModel):
    """Контракт входящего вебхука от шлюзов (GoIP, Dinstar), модемов или SMS-API."""

    kind: InboundKind = "sms"
    phone: str = Field(
        min_length=1,
        max_length=32,
        description="Номер получателя в формате E.164, напр. +79991234567",
    )
    sender: str = Field(
        min_length=1,
        max_length=64,
        description="Имя отправителя или номер, напр. Telegram, WhatsApp, Facebook",
    )
    text: str = Field(default="", max_length=2000, description="Сырой текст SMS-сообщения")
    caller_id: str | None = Field(
        default=None,
        max_length=32,
        description="Caller number of a flash_call; the code is its last digits",
    )
    timestamp: AwareDatetime | None = Field(
        default=None,
        description="When the gateway received it, with a timezone; server time when absent",
    )

    @model_validator(mode="after")
    def _check_flash_call(self) -> InboundSmsWebhook:
        if self.kind == "flash_call" and not self.caller_id:
            msg = "caller_id is required for a flash_call"
            raise ValueError(msg)
        return self


class OtpSession(BaseModel):
    """Активная сессия ожидания SMS для конкретного телефона."""

    phone_id: str = Field(pattern=PHONE_ID_PATTERN)
    phone_number: str = Field(min_length=1)
    platform: TelephonyPlatform = "telegram"
    status: TelephonyLineStatus = "waiting_otp"
    last_code: str | None = None
    last_sms_text: str | None = None
    # The stable refusal code the SPA translates when the wait ended ``failed``.
    error_code: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime
    updated_at: datetime = Field(default_factory=_utc_now)


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
    # The stable refusal code the SPA translates when the wait ended ``failed``.
    error_code: str | None = None
    created_at: datetime | None = None
    expires_at: datetime | None = None
    remaining_seconds: int = Field(default=0, ge=0)


class ManualOtpInputRequest(BaseModel):
    """Запрос на ручной биомеханический ввод OTP-кода оператором."""

    code: str = Field(min_length=4, max_length=8, pattern=OTP_CODE_PATTERN)


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
    timestamp: datetime


class SmsInboxList(BaseModel):
    """Список последних входящих SMS для журнала (до 50 сообщений)."""

    messages: list[SmsInboxMessage]
