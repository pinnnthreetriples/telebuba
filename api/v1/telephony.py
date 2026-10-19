"""Telephony routes: inbound SMS webhook, phone OTP wait and status, manual input, SMS inbox.

``webhook_router`` is mounted OUTSIDE the session gate: SMS gateways (GoIP, Dinstar, a
modem bridge) hold no operator session. It authenticates itself with the shared
``TELEPHONY__WEBHOOK_SECRET`` in the ``X-Telephony-Token`` header (constant-time
compare) and answers 503 while no secret is configured. ``router`` holds the operator
routes and is mounted behind the gate like every other router.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi import status as http_status

from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from core.config import settings
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
webhook_router = APIRouter(prefix="/telephony", tags=["telephony"])

PhoneId = Annotated[str, Path(pattern=PHONE_ID_PATTERN)]


def require_webhook_token(
    x_telephony_token: Annotated[str | None, Header()] = None,
) -> None:
    """Admit a gateway that sends the configured secret; 503 while none is configured."""
    secret = settings.telephony.webhook_secret
    if not secret:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telephony_webhook_disabled",
        )
    if x_telephony_token is None or not hmac.compare_digest(
        x_telephony_token.encode(),
        secret.encode(),
    ):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="telephony_webhook_unauthorized",
        )


@webhook_router.post(
    "/sms-webhook",
    response_model=SmsWebhookResponse,
    operation_id="receiveInboundSmsWebhook",
    dependencies=[Depends(require_webhook_token)],
    responses=SERVICE_ERRORS | error_responses(401, 422, 500),
)
async def receive_inbound_sms_webhook(body: InboundSmsWebhook) -> SmsWebhookResponse:
    """Receive an inbound SMS or Flash Call from a hardware gateway, modem, or SMS API."""
    with service_errors_to_http():
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
    "/phones/{phone_id}/wait",
    response_model=PhoneTelephonyStatus,
    operation_id="startPhoneOtpWait",
    responses=SERVICE_ERRORS,
)
async def start_phone_otp_wait(phone_id: PhoneId) -> PhoneTelephonyStatus:
    """Make the phone wait for an OTP SMS on its account's number; it is typed when it comes."""
    with service_errors_to_http():
        return await telephony_service.start_phone_wait(phone_id)


@router.post(
    "/phones/{phone_id}/manual-input",
    response_model=ManualOtpInputResult,
    operation_id="manualInputPhoneOtp",
    responses=SERVICE_ERRORS,
)
async def manual_input_phone_otp(
    phone_id: PhoneId,
    body: ManualOtpInputRequest,
) -> ManualOtpInputResult:
    """Type an OTP code into the phone's code screen now; refused off that screen."""
    with service_errors_to_http():
        return await telephony_service.manual_input_otp(phone_id, body.code)


@router.get(
    "/inbox",
    response_model=SmsInboxList,
    operation_id="listSmsInbox",
)
async def list_sms_inbox(
    phone_id: Annotated[str | None, Query(pattern=PHONE_ID_PATTERN)] = None,
) -> SmsInboxList:
    """Return the last 50 inbound SMS messages, or only those for one phone."""
    return await telephony_service.list_inbox(phone_id=phone_id)
