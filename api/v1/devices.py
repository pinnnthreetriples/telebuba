"""Device farm endpoints — the phones, and a phone's live screen over a WebSocket.

``router`` is mounted behind the session gate like every other router.
``screen_router`` holds the one WebSocket and authenticates its own handshake: the
gate's dependency re-issues the cookie on a response, which a handshake does not
have. Its Origin is checked earlier, for every handshake (``OriginProtectionMiddleware``).

The socket carries the video as binary frames (the server's bytes as they come) and
the viewer's commands as JSON text frames; the phone's clipboard comes back as a
text frame. It closes with ``1000`` and a ``ScreenCloseCode`` when the screen ends or
is taken over, ``4000`` and the refusal code when it cannot open, ``1008`` on a
command it does not take.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, WebSocket, WebSocketDisconnect
from fastapi import status as http_status

from api.deps import get_websocket_user
from api.errors import SERVICE_ERRORS, error_responses
from api.v1._errors import service_errors_to_http
from schemas.device_farm import (
    PHONE_ID_PATTERN,
    Phone,
    PhoneActionResult,
    PhoneList,
    ScreenClipboard,
    ScreenCloseCode,
    ScreenMode,
)
from schemas.standalone_emulator import (  # noqa: TC001 - FastAPI reads bodies at runtime
    StandaloneEmulatorCreate,
    StandaloneProxyAssign,
)
from services import accounts, device_farm, proxies

router = APIRouter(tags=["devices"])
screen_router = APIRouter(tags=["devices"])

PhoneId = Annotated[str, Path(pattern=PHONE_ID_PATTERN)]

_NORMAL = 1000
_POLICY_VIOLATION = 1008
_REFUSED = 4000
# Longest text frame a command can need (a full clipboard, JSON-escaped).
_MAX_COMMAND_CHARS = 400_000


@router.get("/devices", response_model=PhoneList, operation_id="listPhones")
async def list_phones() -> PhoneList:
    """Every phone (mobile account pod or independent emulator) with its status."""
    return await device_farm.list_phones()


@router.post(
    "/devices/{phone_id}/start",
    response_model=PhoneActionResult,
    operation_id="startPhone",
    responses=SERVICE_ERRORS | error_responses(409),
)
async def start_phone(phone_id: PhoneId) -> PhoneActionResult:
    """Boot the phone in the background, its app untouched (``starting`` at once)."""
    with service_errors_to_http():
        try:
            return await device_farm.start_phone(phone_id)
        except accounts.EmulatorBusyError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc


@router.post(
    "/devices/{phone_id}/stop",
    response_model=PhoneActionResult,
    operation_id="stopPhone",
    responses=SERVICE_ERRORS | error_responses(409),
)
async def stop_phone(phone_id: PhoneId) -> PhoneActionResult:
    """Put the phone to sleep; 409 ``emulator_busy`` while it starts or warms."""
    with service_errors_to_http():
        try:
            return await device_farm.stop_phone(phone_id)
        except accounts.EmulatorBusyError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=exc.code) from exc


@router.post(
    "/devices/standalone",
    response_model=Phone,
    operation_id="createStandalonePhone",
    responses=SERVICE_ERRORS | error_responses(404, 409),
)
async def create_standalone_phone(body: StandaloneEmulatorCreate) -> Phone:
    """A new independent emulator (no account) on a pool proxy; started like any phone.

    404 for an unknown proxy, 409 for one at capacity (it takes a slot like an account).
    """
    with service_errors_to_http():
        try:
            return await device_farm.create_standalone_phone(body)
        except accounts.AccountActionError:
            raise
        except proxies.ProxyCapacityError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc


@router.delete(
    "/devices/standalone/{phone_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="deleteStandalonePhone",
    responses=SERVICE_ERRORS,
)
async def delete_standalone_phone(phone_id: PhoneId) -> None:
    """Remove an independent emulator with its pod and ``/data``."""
    with service_errors_to_http():
        await device_farm.delete_standalone_phone(phone_id)


@router.put(
    "/devices/standalone/{phone_id}/proxy",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="assignStandaloneProxy",
    responses=SERVICE_ERRORS | error_responses(404, 409),
)
async def assign_standalone_proxy(phone_id: PhoneId, body: StandaloneProxyAssign) -> None:
    """Put an independent emulator on a pool proxy; its pod stops (next start uses it)."""
    with service_errors_to_http():
        try:
            await device_farm.set_standalone_phone_proxy(phone_id, body.proxy_id)
        except accounts.AccountActionError:
            raise
        except proxies.ProxyCapacityError as exc:
            raise HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc


@router.delete(
    "/devices/standalone/{phone_id}/proxy",
    status_code=http_status.HTTP_204_NO_CONTENT,
    operation_id="unassignStandaloneProxy",
    responses=SERVICE_ERRORS,
)
async def unassign_standalone_proxy(phone_id: PhoneId) -> None:
    """Take an independent emulator off its proxy; its pod stops."""
    with service_errors_to_http():
        await device_farm.set_standalone_phone_proxy(phone_id, None)


async def _pump_video(websocket: WebSocket, session: device_farm.ScreenSession) -> ScreenCloseCode:
    while chunk := await session.read_video():
        await websocket.send_bytes(chunk)
    return "screen_ended"


async def _pump_commands(
    websocket: WebSocket, session: device_farm.ScreenSession
) -> ScreenCloseCode | None:
    """``None`` once the viewer is gone; a close code for a frame the screen refuses."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        text = message.get("text")
        if not session.controllable or text is None or len(text) > _MAX_COMMAND_CHARS:
            return "screen_bad_command"
        try:
            command = device_farm.parse_command(text)
        except device_farm.ScreenCommandError:
            return "screen_bad_command"
        await session.send(command)


async def _pump_clipboard(
    websocket: WebSocket, session: device_farm.ScreenSession
) -> ScreenCloseCode:
    while (text := await session.read_clipboard()) is not None:
        await websocket.send_text(ScreenClipboard(text=text).model_dump_json())
    return "screen_ended"


async def _superseded(session: device_farm.ScreenSession) -> ScreenCloseCode:
    await session.wait_superseded()
    return "screen_superseded"


async def _relay(
    websocket: WebSocket, session: device_farm.ScreenSession
) -> ScreenCloseCode | None:
    """Run the screen until one side ends it; the reason to close with (``None``: gone)."""
    tasks = [
        asyncio.create_task(_pump_video(websocket, session)),
        asyncio.create_task(_pump_commands(websocket, session)),
        asyncio.create_task(_pump_clipboard(websocket, session)),
        asyncio.create_task(_superseded(session)),
    ]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finished = done.pop()
    if (error := finished.exception()) is not None:
        if isinstance(error, (WebSocketDisconnect, ConnectionError)):
            return None
        raise error
    return finished.result()


@screen_router.websocket("/devices/{phone_id}/screen")
async def phone_screen(
    websocket: WebSocket,
    phone_id: PhoneId,
    mode: Annotated[ScreenMode, Query()] = "view",
) -> None:
    """The phone's live screen; ``mode=control`` also takes the operator's input."""
    if await get_websocket_user(websocket) is None:
        await websocket.close(code=_POLICY_VIOLATION)
        return
    await websocket.accept()
    try:
        async with device_farm.screen_session(phone_id, mode) as session:
            await websocket.send_json({"type": "ready", "controllable": session.controllable})
            ending = await _relay(websocket, session)
    except accounts.AccountActionError as exc:
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.close(code=_REFUSED, reason=exc.code)
        return
    except device_farm.ScreenSupersededError:
        ending = "screen_superseded"
    except (WebSocketDisconnect, ConnectionError):
        return
    if ending is not None:
        code = _POLICY_VIOLATION if ending == "screen_bad_command" else _NORMAL
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.close(code=code, reason=ending)
