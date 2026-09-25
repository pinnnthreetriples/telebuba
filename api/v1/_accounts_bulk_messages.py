"""Start and inspect bulk Telegram message runs from the Accounts screen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.deps import get_current_user
from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from schemas.auth import (
    UserRead,  # noqa: TC001 - FastAPI resolves dependency annotations at runtime
)
from schemas.bulk_messages import (
    BulkMessageGenerated,
    BulkMessageGenerateRequest,
    BulkMessageJob,
    BulkMessageRequest,
)
from services.accounts import bulk_messages

bulk_messages_router = APIRouter()


@bulk_messages_router.post(
    "/accounts/bulk-messages",
    response_model=BulkMessageJob,
    status_code=202,
    operation_id="sendBulkMessages",
    responses=SERVICE_ERRORS,
)
async def send_bulk_messages(
    body: BulkMessageRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[UserRead, Depends(get_current_user)],
) -> BulkMessageJob:
    with service_errors_to_http():
        job = bulk_messages.start_bulk_message_job(body, user.id)
    background_tasks.add_task(bulk_messages.run_bulk_message_job, job.job_id)
    return job


@bulk_messages_router.post(
    "/accounts/bulk-messages/generate",
    response_model=BulkMessageGenerated,
    operation_id="generateBulkMessage",
    responses=SERVICE_ERRORS,
)
async def generate_bulk_message(body: BulkMessageGenerateRequest) -> BulkMessageGenerated:
    with service_errors_to_http():
        return await bulk_messages.generate_bulk_message(body.prompt)


@bulk_messages_router.get(
    "/accounts/bulk-messages/active",
    response_model=BulkMessageJob | None,
    operation_id="getActiveBulkMessageJob",
)
async def get_active_bulk_message_job(
    user: Annotated[UserRead, Depends(get_current_user)],
) -> BulkMessageJob | None:
    return bulk_messages.get_active_bulk_message_job(user.id)


@bulk_messages_router.get(
    "/accounts/bulk-messages/latest",
    response_model=BulkMessageJob | None,
    operation_id="getLatestBulkMessageJob",
)
async def get_latest_bulk_message_job(
    user: Annotated[UserRead, Depends(get_current_user)],
) -> BulkMessageJob | None:
    return bulk_messages.get_latest_bulk_message_job(user.id)


@bulk_messages_router.get(
    "/accounts/bulk-messages/{job_id}",
    response_model=BulkMessageJob,
    operation_id="getBulkMessageJob",
    responses=error_responses(404),
)
async def get_bulk_message_job(
    job_id: str, user: Annotated[UserRead, Depends(get_current_user)]
) -> BulkMessageJob:
    job = bulk_messages.get_bulk_message_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="bulk message job not found")
    return job


@bulk_messages_router.post(
    "/accounts/bulk-messages/{job_id}/cancel",
    response_model=BulkMessageJob,
    operation_id="cancelBulkMessageJob",
    responses=error_responses(404),
)
async def cancel_bulk_message_job(
    job_id: str, user: Annotated[UserRead, Depends(get_current_user)]
) -> BulkMessageJob:
    job = bulk_messages.cancel_bulk_message_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="bulk message job not found")
    return job
