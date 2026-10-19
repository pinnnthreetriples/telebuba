"""Telephony routes: inbound SMS webhook, phone OTP status, manual input, and SMS inbox."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query

from schemas.telephony import (
    PHONE_ID_PATTERN,
    InboundSmsWebhook,
    ManualOtpInputRequest,
    ManualOtpInputResult,
    PhoneTelephonyStatus,
    SmsInboxList,
    SmsWebhookResponse,
)
from services import telephony as telephony_service

router = APIRouter(prefix="/telephony", tags=["telephony"])

PhoneId = Annotated[str, Path(pattern=PHONE_ID_PATTERN)]


@router.post(
    "/sms-webhook",
    response_model=SmsWebhookResponse,
    operation_id="receiveInboundSmsWebhook",
)
async def receive_inbound_sms_webhook(body: InboundSmsWebhook) -> SmsWebhookResponse:
    """Receive an inbound SMS from hardware gateways (GoIP/Dinstar), modems, or SMS API."""
    return await telephony_service.handle_inbound_sms(body)


@router.get(
    "/phones/{phone_id}/status",
    response_model=PhoneTelephonyStatus,
    operation_id="getPhoneTelephonyStatus",
)
async def get_phone_telephony_status(phone_id: PhoneId) -> PhoneTelephonyStatus:
    """Return the current OTP waiting/typing status for a phone container."""
    return await telephony_service.get_phone_status(phone_id)


@router.post(
    "/phones/{phone_id}/manual-input",
    response_model=ManualOtpInputResult,
    operation_id="manualInputPhoneOtp",
)
async def manual_input_phone_otp(
    phone_id: PhoneId,
    body: ManualOtpInputRequest,
) -> ManualOtpInputResult:
    """Manually type an OTP verification code into a phone container from the UI."""
    return await telephony_service.manual_input_otp(phone_id, body.code)


@router.get(
    "/inbox",
    response_model=SmsInboxList,
    operation_id="listSmsInbox",
)
async def list_sms_inbox(
    phone_id: Annotated[str | None, Query(pattern=PHONE_ID_PATTERN)] = None,
) -> SmsInboxList:
    """Return the last 50 inbound SMS messages for the operator inbox drawer."""
    return await telephony_service.list_inbox(phone_id=phone_id)
