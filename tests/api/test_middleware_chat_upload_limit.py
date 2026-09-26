from __future__ import annotations

import pytest

from api import _chat_upload_patterns, _large_upload_patterns
from api._middleware import BodyLimitPolicy, BodySizeLimitMiddleware, Message, Receive, Scope, Send

_BODY = b"x" * 150
_CONTENT_TYPE = b"multipart/form-data; boundary=chat-test"


@pytest.mark.asyncio
async def test_chat_upload_uses_a_separate_total_request_budget() -> None:
    policy = BodyLimitPolicy(
        max_bytes=100,
        max_anonymous_bytes=10,
        cookie_name="tb_session",
        large_upload_path_patterns=(*_large_upload_patterns(), r"/legacy-upload"),
        chat_upload_path_patterns=_chat_upload_patterns(),
        max_chat_upload_bytes=200,
    )

    async def _valid_session(_token: str) -> bool:
        return True

    async def _request(path: str, body: bytes = _BODY) -> int:
        statuses: list[int] = []

        async def _app(_scope: Scope, receive: Receive, send: Send) -> None:
            message = await receive()
            assert not message.get("more_body", False)
            await send({"type": "http.response.start", "status": 204, "headers": []})

        async def _receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        limiter = BodySizeLimitMiddleware(_app, policy, validate_session=_valid_session)
        await limiter(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [
                    (b"content-type", _CONTENT_TYPE),
                    (b"cookie", b"tb_session=valid"),
                ],
            },
            _receive,
            _send,
        )
        return statuses[0]

    # Small budgets prove route selection without allocating a real multi-GB body.
    assert await _request("/api/v1/accounts/account/chats/user/42/messages") == 204
    assert await _request("/api/v1/accounts/account/chats/user/42/messages", b"x" * 201) == 413
    assert await _request("/legacy-upload") == 413
