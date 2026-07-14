"""Channel endpoint tests — thin routes over mocked channel services."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from core.config import settings
from schemas.api import Page
from schemas.channels import (
    ChannelDetailView,
    ChannelPostView,
    ChannelUsernameCheckView,
    ChannelView,
)
from schemas.telegram_actions import ActionResult
from services.accounts import AccountActionError

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _ok(action_type: str, **kwargs: object) -> ActionResult:
    return ActionResult(status="ok", action_type=action_type, account_id="acc-1", **kwargs)  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_create_channel_returns_action_result_with_channel_id(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, body: object) -> ActionResult:
        assert account_id == "acc-1"
        assert body.title == "Mine"  # ty: ignore[unresolved-attribute]
        return _ok("channel_create", channel_id="9007199254740993")

    monkeypatch.setattr("services.accounts.create_account_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels",
            json={"title": "Mine", "about": "Desc", "username": "my_channel"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # int64 survives the JSON boundary as a string.
    assert body["channel_id"] == "9007199254740993"


@pytest.mark.asyncio
async def test_create_channel_maps_value_error_to_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(account_id: str, body: object) -> ActionResult:  # noqa: ARG001
        msg = "channel photo must be one of: .jpg"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.create_account_channel", _boom)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/channels", json={"title": "Mine"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_list_channels_returns_page(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str) -> Page[ChannelView]:
        assert account_id == "acc-1"
        return Page(
            items=[ChannelView(channel_id="42", title="Mine", participants_count=5)],
            next_cursor=None,
        )

    monkeypatch.setattr("services.accounts.list_account_channels", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/channels")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["channel_id"] == "42"
    assert body["next_cursor"] is None


@pytest.mark.asyncio
async def test_check_channel_username_returns_verdict(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, username: str) -> ChannelUsernameCheckView:  # noqa: ARG001
        assert username == "my_channel"
        return ChannelUsernameCheckView(available=False, code="channel_username_occupied")

    monkeypatch.setattr("services.accounts.check_account_channel_username", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/accounts/acc-1/channel-username-check",
            params={"username": "my_channel"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["code"] == "channel_username_occupied"


@pytest.mark.asyncio
async def test_get_channel_returns_detail(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, channel_id: int) -> ChannelDetailView:  # noqa: ARG001
        assert channel_id == 42
        return ChannelDetailView(channel_id="42", title="Mine", about="Desc")

    monkeypatch.setattr("services.accounts.get_account_channel", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/channels/42")
    assert resp.status_code == 200
    assert resp.json()["about"] == "Desc"


@pytest.mark.asyncio
async def test_get_channel_invalid_id_is_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/channels/not-a-number")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_get_channel_non_positive_id_is_400(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/channels/0")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_channel_passes_body(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(account_id: str, channel_id: int, body: object) -> ActionResult:  # noqa: ARG001
        assert channel_id == 42
        assert body.title == "New"  # ty: ignore[unresolved-attribute]
        return _ok("channel_edit")

    monkeypatch.setattr("services.accounts.update_account_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels/42/update",
            json={"title": "New"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_set_channel_photo_accepts_multipart(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(
        account_id: str,  # noqa: ARG001
        channel_id: int,
        *,
        filename: str,
        content: bytes,
    ) -> ActionResult:
        assert channel_id == 42
        assert filename == "logo.png"
        assert content == b"png-bytes"
        return _ok("channel_set_photo")

    monkeypatch.setattr("services.accounts.set_account_channel_photo", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels/42/photo",
            files={"file": ("logo.png", b"png-bytes", "image/png")},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_set_channel_photo_declared_oversize_is_rejected_before_read(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.channels, "avatar_max_bytes", 4)

    async def _never(*_args: object, **_kwargs: object) -> ActionResult:  # pragma: no cover
        msg = "service must not be reached for an oversize upload"
        raise AssertionError(msg)

    monkeypatch.setattr("services.accounts.set_account_channel_photo", _never)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels/42/photo",
            files={"file": ("logo.png", b"way-too-big", "image/png")},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_delete_channel_calls_service(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[int] = []

    async def _fake(account_id: str, channel_id: int) -> ActionResult:  # noqa: ARG001
        deleted.append(channel_id)
        return _ok("channel_delete")

    monkeypatch.setattr("services.accounts.delete_account_channel", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/channels/42/delete")
    assert resp.status_code == 200
    assert deleted == [42]


@pytest.mark.asyncio
async def test_publish_post_multipart_with_file(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(
        account_id: str,  # noqa: ARG001
        channel_id: int,
        *,
        text: str,
        filename: str | None,
        content: bytes | None,
    ) -> ActionResult:
        assert channel_id == 42
        assert text == "caption"
        assert filename == "pic.jpg"
        assert content == b"jpg-bytes"
        return _ok("channel_post_publish", message_id=7)

    monkeypatch.setattr("services.accounts.publish_account_channel_post", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels/42/posts",
            data={"text": "caption"},
            files={"file": ("pic.jpg", b"jpg-bytes", "image/jpeg")},
        )
    assert resp.status_code == 200
    assert resp.json()["message_id"] == 7


@pytest.mark.asyncio
async def test_publish_post_text_only(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(
        account_id: str,  # noqa: ARG001
        channel_id: int,  # noqa: ARG001
        *,
        text: str,
        filename: str | None,
        content: bytes | None,
    ) -> ActionResult:
        assert text == "just text"
        assert filename is None
        assert content is None
        return _ok("channel_post_publish", message_id=8)

    monkeypatch.setattr("services.accounts.publish_account_channel_post", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels/42/posts",
            data={"text": "just text"},
        )
    assert resp.status_code == 200
    assert resp.json()["message_id"] == 8


@pytest.mark.asyncio
async def test_list_posts_forwards_cursor_and_limit(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(
        account_id: str,  # noqa: ARG001
        channel_id: int,
        *,
        cursor: str | None,
        limit: int | None,
    ) -> Page[ChannelPostView]:
        assert channel_id == 42
        assert cursor == "98"
        assert limit == 10
        return Page(
            items=[ChannelPostView(post_id=97, date_unix=1_750_000_000, text="hi")],
            next_cursor=None,
        )

    monkeypatch.setattr("services.accounts.list_account_channel_posts", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/accounts/acc-1/channels/42/posts",
            params={"cursor": "98", "limit": 10},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["post_id"] == 97
    assert body["items"][0]["media_kind"] == "none"


@pytest.mark.asyncio
async def test_list_posts_bad_cursor_is_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(
        account_id: str,  # noqa: ARG001
        channel_id: int,  # noqa: ARG001
        *,
        cursor: str | None,  # noqa: ARG001
        limit: int | None,  # noqa: ARG001
    ) -> Page[ChannelPostView]:
        msg = "invalid pagination cursor"
        raise ValueError(msg)

    monkeypatch.setattr("services.accounts.list_account_channel_posts", _boom)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/accounts/acc-1/channels/42/posts",
            params={"cursor": "abc"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "invalid pagination cursor"


@pytest.mark.asyncio
async def test_edit_post_passes_text(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(
        account_id: str,  # noqa: ARG001
        channel_id: int,
        post_id: int,
        *,
        text: str,
    ) -> ActionResult:
        assert channel_id == 42
        assert post_id == 10
        assert text == "fixed"
        return _ok("channel_post_edit")

    monkeypatch.setattr("services.accounts.edit_account_channel_post", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels/42/posts/10/edit",
            json={"text": "fixed"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_delete_post_calls_service(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[tuple[int, int]] = []

    async def _fake(account_id: str, channel_id: int, post_id: int) -> ActionResult:  # noqa: ARG001
        deleted.append((channel_id, post_id))
        return _ok("channel_post_delete")

    monkeypatch.setattr("services.accounts.delete_account_channel_post", _fake)
    async with _client(app) as client:
        resp = await client.post("/api/v1/accounts/acc-1/channels/42/posts/10/delete")
    assert resp.status_code == 200
    assert deleted == [(42, 10)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("service_name", "method", "url", "payload"),
    [
        (
            "update_account_channel",
            "post",
            "/api/v1/accounts/acc-1/channels/42/update",
            {"json": {"title": "New"}},
        ),
        (
            "set_account_channel_photo",
            "post",
            "/api/v1/accounts/acc-1/channels/42/photo",
            {"files": {"file": ("logo.png", b"png", "image/png")}},
        ),
        (
            "delete_account_channel",
            "post",
            "/api/v1/accounts/acc-1/channels/42/delete",
            {},
        ),
        (
            "publish_account_channel_post",
            "post",
            "/api/v1/accounts/acc-1/channels/42/posts",
            {"data": {"text": "hi"}},
        ),
        (
            "edit_account_channel_post",
            "post",
            "/api/v1/accounts/acc-1/channels/42/posts/10/edit",
            {"json": {"text": "fixed"}},
        ),
        (
            "delete_account_channel_post",
            "post",
            "/api/v1/accounts/acc-1/channels/42/posts/10/delete",
            {},
        ),
    ],
)
async def test_channel_routes_map_value_error_to_400(  # noqa: PLR0913 - one param per route facet
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    service_name: str,
    method: str,
    url: str,
    payload: dict[str, object],
) -> None:
    """Every mutating channel route maps a service ValueError to 400."""

    async def _boom(*_args: object, **_kwargs: object) -> ActionResult:
        msg = "validation refused"
        raise ValueError(msg)

    monkeypatch.setattr(f"services.accounts.{service_name}", _boom)
    async with _client(app) as client:
        resp = await client.request(method.upper(), url, **payload)  # ty: ignore[invalid-argument-type]
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_list_posts_action_error_passes_through_envelope(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _refused(*_args: object, **_kwargs: object) -> Page[ChannelPostView]:
        code = "channel_read_failed"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.list_account_channel_posts", _refused)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/channels/42/posts")
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "channel_read_failed"


@pytest.mark.asyncio
async def test_channel_action_error_envelope_carries_stable_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AccountActionError passes through to the envelope: code in message."""

    async def _refused(account_id: str, body: object) -> ActionResult:  # noqa: ARG001
        code = "channel_username_occupied"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.create_account_channel", _refused)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/accounts/acc-1/channels",
            json={"title": "Mine", "username": "taken_name"},
        )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "channel_username_occupied"


@pytest.mark.asyncio
async def test_channel_unavailable_maps_to_503(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gateway infrastructure failure is a 503, not a client fault."""

    async def _unavailable(account_id: str) -> Page[ChannelView]:  # noqa: ARG001
        code = "unavailable"
        raise AccountActionError(code)

    monkeypatch.setattr("services.accounts.list_account_channels", _unavailable)
    async with _client(app) as client:
        resp = await client.get("/api/v1/accounts/acc-1/channels")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "unavailable"
