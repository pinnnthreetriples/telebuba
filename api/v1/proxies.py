"""Proxy-pool endpoints — thin routes over ``services.proxies``.

The Accounts screen's proxy-pool card: list pool proxies (with usage), add a
proxy, probe connectivity, assign/unassign an account, delete a proxy.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi import status as http_status

from schemas.proxy import ProxyAssignRequest, ProxyCheckResult, ProxyCreate, ProxyList, ProxyRead
from services import proxies

router = APIRouter(tags=["proxies"])


@router.get("/proxies", response_model=ProxyList, operation_id="listProxies")
async def list_proxies() -> ProxyList:
    return await proxies.list_pool()


@router.post("/proxies", response_model=ProxyRead, operation_id="createProxy")
async def create_proxy(body: ProxyCreate) -> ProxyRead:
    return await proxies.add_proxy(body)


@router.post("/proxies/probe", response_model=ProxyCheckResult, operation_id="probeProxy")
async def probe_proxy(body: ProxyCreate) -> ProxyCheckResult:
    return await proxies.probe_proxy(body)


@router.post("/proxies/{proxy_id}/check", response_model=ProxyRead, operation_id="checkProxy")
async def check_proxy(proxy_id: str) -> ProxyRead:
    try:
        return await proxies.check_proxy(proxy_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post("/proxies/{proxy_id}/assign", response_model=ProxyRead, operation_id="assignProxy")
async def assign_proxy(proxy_id: str, body: ProxyAssignRequest) -> ProxyRead:
    try:
        return await proxies.assign_proxy(proxy_id, body.account_id)
    except proxies.ProxyCapacityError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/proxies/unassign",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="unassignProxy",
)
async def unassign_proxy(body: ProxyAssignRequest) -> None:
    await proxies.unassign_proxy(body.account_id)


@router.delete(
    "/proxies/{proxy_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteProxy",
)
async def delete_proxy(proxy_id: str) -> None:
    await proxies.remove_proxy(proxy_id)
