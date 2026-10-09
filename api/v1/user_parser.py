"""User parser — thin routes over ``services.user_parser``.

A start is background work: ``POST /runs`` answers ``202`` with a status, and a refusal
(an account busy, cooling, unknown) is that status naming the account, not an error. The
modal then polls ``GET /runs/{id}``. Bases are settled runs; an unknown one answers 404,
deleting one still collecting 409. A preset name another preset has answers 409.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi import status as http_status
from fastapi.responses import StreamingResponse

from api.errors import error_responses
from schemas.user_parser import (
    PRESET_NAME_TAKEN,
    RUN_STILL_RUNNING,
    UserParserAccountList,
    UserParserPreset,
    UserParserPresetList,
    UserParserPresetWrite,
    UserParserRequest,
    UserParserStartOutcome,
)
from schemas.user_parser_run import (
    ExportFormat,
    UserParserBase,
    UserParserBaseList,
    UserParserBaseRename,
    UserParserRun,
    UserParserUserPage,
)
from services import user_parser as parser

router = APIRouter(prefix="/user-parser", tags=["user-parser"])

RunIdPath = Annotated[str, Path(min_length=1, max_length=64)]
_PAGE_MAX = 500


_RUN_NOT_FOUND = "run not found"
_BASE_NOT_FOUND = "base not found"
_PRESET_NOT_FOUND = "preset not found"


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=detail)


@router.get(
    "/accounts", response_model=UserParserAccountList, operation_id="listUserParserAccounts"
)
async def list_user_parser_accounts() -> UserParserAccountList:
    """Every account the parser may read with, busy ones marked with why."""
    return await parser.list_reading_accounts()


@router.post(
    "/runs",
    response_model=UserParserStartOutcome,
    status_code=http_status.HTTP_202_ACCEPTED,
    operation_id="startUserParserRun",
)
async def start_user_parser_run(body: UserParserRequest) -> UserParserStartOutcome:
    try:
        return await parser.start_run(body)
    except parser.UserParserInvalidError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.code
        ) from exc


@router.get(
    "/runs/{run_id}",
    response_model=UserParserRun,
    operation_id="getUserParserRun",
    responses=error_responses(404),
)
async def get_user_parser_run(run_id: RunIdPath) -> UserParserRun:
    run = await parser.get_run(run_id)
    if run is None:
        raise _not_found(_RUN_NOT_FOUND)
    return run


@router.post(
    "/runs/{run_id}/stop",
    response_model=UserParserRun,
    operation_id="stopUserParserRun",
    responses=error_responses(404),
)
async def stop_user_parser_run(run_id: RunIdPath) -> UserParserRun:
    """Stop a run; whatever it collected is saved as its base."""
    run = await parser.stop_run(run_id)
    if run is None:
        raise _not_found(_RUN_NOT_FOUND)
    return run


async def _users(run_id: str, search: str, offset: int, limit: int) -> UserParserUserPage:
    page = await parser.list_base_users(run_id, search=search, offset=offset, limit=limit)
    if page is None:
        raise _not_found(_RUN_NOT_FOUND)
    return page


@router.get(
    "/runs/{run_id}/users",
    response_model=UserParserUserPage,
    operation_id="listUserParserRunUsers",
    responses=error_responses(404),
)
async def list_user_parser_run_users(
    run_id: RunIdPath,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_PAGE_MAX)] = 100,
) -> UserParserUserPage:
    return await _users(run_id, "", offset, limit)


@router.get(
    "/runs/{run_id}/export",
    operation_id="exportUserParserRun",
    responses=error_responses(404),
)
async def export_user_parser_run(
    run_id: RunIdPath,
    format: Annotated[ExportFormat, Query()] = "csv",  # noqa: A002 - the query parameter's name
) -> StreamingResponse:
    """The base as a file: CSV (UTF-8 with BOM, opens in Excel) or JSON."""
    export = await parser.export_base(run_id, format)
    if export is None:
        raise _not_found(_RUN_NOT_FOUND)
    filename, media_type, chunks = export
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(chunks, media_type=media_type, headers=headers)


@router.get("/presets", response_model=UserParserPresetList, operation_id="listUserParserPresets")
async def list_user_parser_presets() -> UserParserPresetList:
    return await parser.list_presets()


@router.post(
    "/presets",
    response_model=UserParserPreset,
    operation_id="createUserParserPreset",
    responses=error_responses(409),
)
async def create_user_parser_preset(body: UserParserPresetWrite) -> UserParserPreset:
    try:
        return await parser.create_preset(body.name, body.settings)
    except parser.PresetNameTakenError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail=PRESET_NAME_TAKEN
        ) from exc


@router.delete(
    "/presets/{preset_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteUserParserPreset",
    responses=error_responses(404),
)
async def delete_user_parser_preset(preset_id: RunIdPath) -> None:
    if not await parser.delete_preset(preset_id):
        raise _not_found(_PRESET_NOT_FOUND)


@router.get("/bases", response_model=UserParserBaseList, operation_id="listUserParserBases")
async def list_user_parser_bases() -> UserParserBaseList:
    """Every settled run as a folder, newest first. Nothing deletes one but the operator."""
    return await parser.list_bases()


@router.patch(
    "/bases/{run_id}",
    response_model=UserParserBase,
    operation_id="renameUserParserBase",
    responses=error_responses(404),
)
async def rename_user_parser_base(run_id: RunIdPath, body: UserParserBaseRename) -> UserParserBase:
    renamed = await parser.rename_base(run_id, body.name)
    if renamed is None:
        raise _not_found(_BASE_NOT_FOUND)
    return renamed


@router.delete(
    "/bases/{run_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteUserParserBase",
    responses=error_responses(404, 409),
)
async def delete_user_parser_base(run_id: RunIdPath) -> None:
    """Delete a base and every person in it, for good."""
    try:
        deleted = await parser.delete_base(run_id)
    except parser.BaseRunningError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT, detail=RUN_STILL_RUNNING
        ) from exc
    if not deleted:
        raise _not_found(_BASE_NOT_FOUND)


@router.get(
    "/bases/{run_id}/users",
    response_model=UserParserUserPage,
    operation_id="listUserParserBaseUsers",
    responses=error_responses(404),
)
async def list_user_parser_base_users(
    run_id: RunIdPath,
    search: Annotated[str, Query(max_length=100)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=_PAGE_MAX)] = 100,
) -> UserParserUserPage:
    return await _users(run_id, search, offset, limit)
