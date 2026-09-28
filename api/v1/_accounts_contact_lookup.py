"""Start and inspect phone-number lookup runs from the Accounts screen."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.deps import get_current_user
from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from schemas.auth import (
    UserRead,  # noqa: TC001 - FastAPI resolves dependency annotations at runtime
)
from schemas.contact_lookup import ContactLookupJob, ContactLookupRequest
from services.accounts import contact_lookup

contact_lookup_router = APIRouter()


@contact_lookup_router.post(
    "/accounts/contact-lookup",
    response_model=ContactLookupJob,
    status_code=202,
    operation_id="startContactLookup",
    responses=SERVICE_ERRORS,
)
async def start_contact_lookup(
    body: ContactLookupRequest,
    background_tasks: BackgroundTasks,
    user: Annotated[UserRead, Depends(get_current_user)],
) -> ContactLookupJob:
    with service_errors_to_http():
        job = contact_lookup.start_contact_lookup_job(body, user.id)
    background_tasks.add_task(contact_lookup.run_contact_lookup_job, job.job_id)
    return job


@contact_lookup_router.get(
    "/accounts/contact-lookup/active",
    response_model=ContactLookupJob | None,
    operation_id="getActiveContactLookupJob",
)
async def get_active_contact_lookup_job(
    user: Annotated[UserRead, Depends(get_current_user)],
) -> ContactLookupJob | None:
    return contact_lookup.get_active_contact_lookup_job(user.id)


@contact_lookup_router.get(
    "/accounts/contact-lookup/latest",
    response_model=ContactLookupJob | None,
    operation_id="getLatestContactLookupJob",
)
async def get_latest_contact_lookup_job(
    user: Annotated[UserRead, Depends(get_current_user)],
) -> ContactLookupJob | None:
    return contact_lookup.get_latest_contact_lookup_job(user.id)


@contact_lookup_router.get(
    "/accounts/contact-lookup/{job_id}",
    response_model=ContactLookupJob,
    operation_id="getContactLookupJob",
    responses=error_responses(404),
)
async def get_contact_lookup_job(
    job_id: str, user: Annotated[UserRead, Depends(get_current_user)]
) -> ContactLookupJob:
    job = contact_lookup.get_contact_lookup_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="contact lookup job not found")
    return job


@contact_lookup_router.post(
    "/accounts/contact-lookup/{job_id}/cancel",
    response_model=ContactLookupJob,
    operation_id="cancelContactLookupJob",
    responses=error_responses(404),
)
async def cancel_contact_lookup_job(
    job_id: str, user: Annotated[UserRead, Depends(get_current_user)]
) -> ContactLookupJob:
    job = contact_lookup.cancel_contact_lookup_job(job_id, user.id)
    if job is None:
        raise HTTPException(status_code=404, detail="contact lookup job not found")
    return job
