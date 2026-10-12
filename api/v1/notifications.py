"""Operator-bot notification endpoints — settings (token masked) and a test send."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from api.errors import error_responses
from schemas.notifications import (
    NotificationSettings,
    NotificationSettingsUpdate,
    NotificationTestResult,
)
from services import notification_settings as notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "/settings",
    response_model=NotificationSettings,
    operation_id="getNotificationSettings",
)
async def get_notification_settings() -> NotificationSettings:
    return await notification_service.load_settings()


@router.put(
    "/settings",
    response_model=NotificationSettings,
    operation_id="updateNotificationSettings",
)
async def update_notification_settings(body: NotificationSettingsUpdate) -> NotificationSettings:
    return await notification_service.save_settings(body)


@router.post(
    "/test",
    response_model=NotificationTestResult,
    operation_id="sendTestNotification",
    responses=error_responses(400, 409, 503),
)
async def send_test_notification() -> NotificationTestResult:
    """Send the test message with the stored token and chat.

    409 ``notifications_not_configured``: no token or no chat id stored. 400
    ``telegram_rejected``: Telegram refused it (bad token, unknown chat, bot blocked).
    503 ``telegram_unreachable``: the network or a timeout.
    """
    try:
        return await notification_service.send_test_notification()
    except notification_service.NotificationsNotConfiguredError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="notifications_not_configured",
        ) from exc
    except notification_service.NotificationDeliveryError as exc:
        if exc.status == "rejected":
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="telegram_rejected",
            ) from exc
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telegram_unreachable",
        ) from exc
