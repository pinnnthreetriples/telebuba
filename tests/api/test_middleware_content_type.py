"""Content-Type admission regressions for the large upload body budget."""

from __future__ import annotations

import pytest

from api._middleware import (
    BodyLimitPolicy,
    BodySizeLimitMiddleware,
    Message,
    Receive,
    Scope,
    Send,
)


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/octet-stream",
        "multipart/form-data",
        "multipart/form-data; boundary=first; boundary=second",
    ],
)
@pytest.mark.asyncio
async def test_valid_session_never_buys_large_budget_without_valid_multipart(
    content_type: str,
) -> None:
    sent: list[Message] = []
    validations = 0

    async def _app(_scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})

    async def _valid(_token: str) -> bool:
        nonlocal validations
        validations += 1
        return True

    async def _oversized_receive() -> Message:
        return {"type": "http.request", "body": b"x" * 11, "more_body": False}

    async def _capture(message: Message) -> None:
        sent.append(message)

    wrapped = BodySizeLimitMiddleware(
        _app,
        BodyLimitPolicy(
            max_bytes=100,
            max_anonymous_bytes=10,
            cookie_name="tb_session",
            large_upload_path_patterns=(r"/upload",),
        ),
        validate_session=_valid,
    )
    await wrapped(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [
                (b"content-type", content_type.encode()),
                (b"cookie", b"tb_session=valid"),
            ],
        },
        _oversized_receive,
        _capture,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413
    assert validations == 0
