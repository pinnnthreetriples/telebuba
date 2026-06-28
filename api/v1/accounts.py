"""Accounts read endpoints — thin routes over ``services.accounts``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http_status

from schemas.accounts import AccountRead
from schemas.api import Page
from services import accounts

router = APIRouter(tags=["accounts"])


@router.get("/accounts", response_model=Page[AccountRead], operation_id="listAccounts")
async def list_accounts(
    query: str = "",
    status: str = "all",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[AccountRead]:
    try:
        return await accounts.list_accounts_page(
            query=query,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    except accounts.InvalidCursorError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid pagination cursor",
        ) from exc
