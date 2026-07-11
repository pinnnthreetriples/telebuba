"""Neurocomment endpoint tests — thin routes over a mocked services.neurocomment."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from schemas.api import Page
from schemas.challenge import ChallengeOutcomeCounts, ChallengeRow, ChallengeRowList
from schemas.neurocomment import (
    AccountChannelOnboarding,
    CampaignList,
    ChannelLinkOutcome,
    CommentRecord,
    NeurocommentBoard,
    NeurocommentCampaign,
    NeurocommentRuntimeStatus,
    NeurocommentSettings,
)
from services.neurocomment import (
    ChannelNotInCampaignError,
    InvalidCursorError,
    ListenerBusyWarmingError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _campaign() -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id="c1",
        name="Promo",
        prompt="mention the product",
        status="active",
        created_at="now",
        updated_at="now",
    )


@pytest.mark.asyncio
async def test_list_campaigns(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake() -> CampaignList:
        return CampaignList(campaigns=[_campaign()])

    monkeypatch.setattr("services.neurocomment.list_campaigns", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns")
    assert resp.status_code == 200
    assert [c["campaign_id"] for c in resp.json()["campaigns"]] == ["c1"]


@pytest.mark.asyncio
async def test_list_campaign_challenges(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, limit: int) -> ChallengeRowList:  # noqa: ARG001
        return ChallengeRowList(
            rows=[
                ChallengeRow(
                    account_id="acc-1",
                    channel="@a",
                    raw_text="captcha",
                    outcome="failed",
                    decided_at="2026-06-30T12:00:00+00:00",
                ),
            ],
        )

    monkeypatch.setattr("services.neurocomment.list_campaign_challenges", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/c1/challenges")
    assert resp.status_code == 200
    assert [r["channel"] for r in resp.json()["rows"]] == ["@a"]


def _settings() -> NeurocommentSettings:
    return NeurocommentSettings(
        max_comments_per_hour=10,
        max_comments_per_channel_per_day=3,
        reply_delay_min_seconds=3.0,
        reply_delay_max_seconds=10.0,
        min_trust_score=45,
        updated_at="now",
    )


@pytest.mark.asyncio
async def test_get_and_update_neuro_settings(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _load() -> NeurocommentSettings:
        return _settings()

    async def _save(body: object) -> NeurocommentSettings:  # noqa: ARG001
        return _settings()

    monkeypatch.setattr("services.neurocomment.load_neurocomment_settings", _load)
    monkeypatch.setattr("services.neurocomment.save_neurocomment_settings", _save)
    async with _client(app) as client:
        got = await client.get("/api/v1/neurocomment/settings")
        put = await client.put(
            "/api/v1/neurocomment/settings",
            json={
                "max_comments_per_hour": 10,
                "max_comments_per_channel_per_day": 3,
                "reply_delay_min_seconds": 3.0,
                "reply_delay_max_seconds": 10.0,
                "min_trust_score": 45,
            },
        )
    assert got.status_code == 200
    assert got.json()["min_trust_score"] == 45
    assert put.status_code == 200


@pytest.mark.asyncio
async def test_create_campaign(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(body: object) -> NeurocommentCampaign:  # noqa: ARG001
        return _campaign()

    monkeypatch.setattr("services.neurocomment.create_campaign", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns",
            json={"name": "Promo", "prompt": "mention the product"},
        )
    assert resp.status_code == 200
    assert resp.json()["campaign_id"] == "c1"


@pytest.mark.asyncio
async def test_board_found(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str) -> NeurocommentBoard:
        return NeurocommentBoard(campaign_id=campaign_id, campaign_name="Promo", status="active")

    monkeypatch.setattr("services.neurocomment.load_neurocomment_board", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/c1/board")
    assert resp.status_code == 200
    assert resp.json()["campaign_name"] == "Promo"


@pytest.mark.asyncio
async def test_board_missing_is_404(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(campaign_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.load_neurocomment_board", _none)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/ghost/board")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_set_account_channel_returns_board(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def _fake(campaign_id: str, account_id: str, channel: str | None) -> NeurocommentBoard:
        seen.update(campaign_id=campaign_id, account_id=account_id, channel=channel)
        return NeurocommentBoard(campaign_id=campaign_id, campaign_name="Promo", status="active")

    monkeypatch.setattr("services.neurocomment.pin_account_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/accounts/acc-1/channel",
            json={"channel": "@news"},
        )
    assert resp.status_code == 200
    assert resp.json()["campaign_name"] == "Promo"
    assert seen == {"campaign_id": "c1", "account_id": "acc-1", "channel": "@news"}


@pytest.mark.asyncio
async def test_set_account_channel_null_clears_pin(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}

    async def _fake(campaign_id: str, account_id: str, channel: str | None) -> NeurocommentBoard:  # noqa: ARG001
        seen["channel"] = channel
        return NeurocommentBoard(campaign_id=campaign_id, campaign_name="Promo", status="active")

    monkeypatch.setattr("services.neurocomment.pin_account_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/accounts/acc-1/channel",
            json={"channel": None},
        )
    assert resp.status_code == 200
    assert seen == {"channel": None}


@pytest.mark.asyncio
async def test_set_account_channel_foreign_channel_is_400(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _boom(campaign_id: str, account_id: str, channel: str | None) -> NeurocommentBoard:  # noqa: ARG001
        raise ChannelNotInCampaignError

    monkeypatch.setattr("services.neurocomment.pin_account_channel", _boom)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/accounts/acc-1/channel",
            json={"channel": "@other"},
        )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_set_account_channel_missing_campaign_is_404(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _none(campaign_id: str, account_id: str, channel: str | None) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.pin_account_channel", _none)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/ghost/accounts/acc-1/channel",
            json={"channel": None},
        )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_link_channel(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, channel: str) -> ChannelLinkOutcome:  # noqa: ARG001
        return ChannelLinkOutcome(status="linked", channel=channel)

    monkeypatch.setattr("services.neurocomment.link_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/channels",
            json={"channel": "@news"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "linked", "channel": "@news"}


@pytest.mark.asyncio
async def test_assign_account_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, account_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.assign_account_to_campaign", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/accounts",
            json={"account_id": "acc-1"},
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_remove_account_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    async def _fake(campaign_id: str, account_id: str) -> None:
        seen["campaign_id"] = campaign_id
        seen["account_id"] = account_id

    monkeypatch.setattr("services.neurocomment.remove_account_from_campaign", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/accounts/remove",
            json={"account_id": "acc-1"},
        )
    assert resp.status_code == 204
    assert seen == {"campaign_id": "c1", "account_id": "acc-1"}


@pytest.mark.asyncio
async def test_start_runtime(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _start(listener_account_id: str) -> None:  # noqa: ARG001
        return None

    async def _status() -> NeurocommentRuntimeStatus:
        return NeurocommentRuntimeStatus(
            running=True,
            active_channels=2,
            listener_account_id="acc-1",
            log_limit=50,
        )

    monkeypatch.setattr("services.neurocomment.start_neurocomment", _start)
    monkeypatch.setattr("services.neurocomment.neurocomment_runtime_status", _status)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/start",
            json={"listener_account_id": "acc-1"},
        )
    assert resp.status_code == 200
    assert resp.json()["running"] is True
    # The activity-log row cap is served from config, not hardcoded in the SPA (#7).
    assert resp.json()["log_limit"] == 50


@pytest.mark.asyncio
async def test_start_runtime_warming_listener_is_409(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _start(listener_account_id: str) -> None:
        raise ListenerBusyWarmingError(listener_account_id)

    monkeypatch.setattr("services.neurocomment.start_neurocomment", _start)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/start",
            json={"listener_account_id": "acc-1"},
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_clear_listener_runtime(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /listener/clear removes the listener and returns the (empty) runtime status."""
    called = False

    async def _clear() -> None:
        nonlocal called
        called = True

    async def _status() -> NeurocommentRuntimeStatus:
        return NeurocommentRuntimeStatus(running=False, listener_account_id=None, log_limit=50)

    monkeypatch.setattr("services.neurocomment.clear_neurocomment_listener", _clear)
    monkeypatch.setattr("services.neurocomment.neurocomment_runtime_status", _status)
    async with _client(app) as client:
        resp = await client.post("/api/v1/neurocomment/listener/clear")
    assert resp.status_code == 200
    assert called is True
    assert resp.json()["running"] is False
    assert resp.json()["listener_account_id"] is None


@pytest.mark.asyncio
async def test_skip_pair_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    async def _fake(account_id: str, channel: str) -> None:
        seen["account_id"] = account_id
        seen["channel"] = channel

    monkeypatch.setattr("services.neurocomment.skip_pair", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/skip",
            json={"account_id": "acc-1", "channel": "@news"},
        )
    assert resp.status_code == 204
    assert seen == {"account_id": "acc-1", "channel": "@news"}


@pytest.mark.asyncio
async def test_list_channel_challenges(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(channel: str, limit: int) -> ChallengeRowList:  # noqa: ARG001
        return ChallengeRowList(
            rows=[
                ChallengeRow(
                    account_id="acc-1",
                    channel=channel,
                    raw_text="captcha",
                    outcome="failed",
                    decided_at="2026-06-30T12:00:00+00:00",
                ),
            ],
        )

    monkeypatch.setattr("services.neurocomment.list_channel_challenges", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/neurocomment/channels/challenges",
            params={"channel": "@news"},
        )
    assert resp.status_code == 200
    assert [r["channel"] for r in resp.json()["rows"]] == ["@news"]


@pytest.mark.asyncio
async def test_count_campaign_challenge_outcomes(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    async def _fake(campaign_id: str, since: str) -> ChallengeOutcomeCounts:
        seen["campaign_id"] = campaign_id
        seen["since"] = since
        return ChallengeOutcomeCounts(solved=3, failed=1, give_up=0, pending=2)

    monkeypatch.setattr("services.neurocomment.count_campaign_challenge_outcomes", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/neurocomment/campaigns/c1/challenges/counts",
            params={"since": "2026-06-30T00:00:00+00:00"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"solved": 3, "failed": 1, "give_up": 0, "pending": 2}
    assert seen == {"campaign_id": "c1", "since": "2026-06-30T00:00:00+00:00"}


@pytest.mark.asyncio
async def test_set_campaign_status_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    async def _fake(campaign_id: str, status: str) -> None:
        seen["campaign_id"] = campaign_id
        seen["status"] = status

    monkeypatch.setattr("services.neurocomment.set_campaign_status", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/status",
            json={"status": "paused"},
        )
    assert resp.status_code == 204
    assert seen == {"campaign_id": "c1", "status": "paused"}


@pytest.mark.asyncio
async def test_set_campaign_status_rejects_bad_status(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(campaign_id: str, status: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.set_campaign_status", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/status",
            json={"status": "archived"},  # not a run/pause status
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_set_solver_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, value: object) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.set_solver_enabled", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/solver",
            json={"enabled": False},
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_retry_challenge_reonboards(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(account_id: str, channel: str) -> AccountChannelOnboarding:
        return AccountChannelOnboarding(account_id=account_id, channel=channel, state="ready")

    monkeypatch.setattr("services.neurocomment.retry_pair", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/retry",
            json={"account_id": "acc-1", "channel": "@news"},
        )
    assert resp.status_code == 200
    assert resp.json()["state"] == "ready"


@pytest.mark.asyncio
async def test_delete_campaign_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.delete_campaign", _fake)
    async with _client(app) as client:
        resp = await client.delete("/api/v1/neurocomment/campaigns/c1")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_remove_channel_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, channel: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.deactivate_channel", _fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/c1/channels/remove",
            json={"channel": "@news"},
        )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_update_prompt_is_204(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, prompt: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.update_campaign_prompt", _fake)
    async with _client(app) as client:
        resp = await client.put(
            "/api/v1/neurocomment/campaigns/c1/prompt",
            json={"prompt": "be nice"},
        )
    assert resp.status_code == 204


def _comment(post_id: int) -> CommentRecord:
    return CommentRecord(
        channel="@chan",
        post_id=post_id,
        campaign_id="c1",
        account_id="acc-1",
        status="posted",
        comment_text=f"c{post_id}",
        comment_msg_id=post_id,
        created_at="2026-07-11T10:00:00+00:00",
        updated_at="2026-07-11T10:00:00+00:00",
    )


@pytest.mark.asyncio
async def test_list_comments_ok_shape(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(campaign_id: str, cursor: str | None, limit: int) -> Page[CommentRecord]:  # noqa: ARG001
        return Page(items=[_comment(2), _comment(1)], next_cursor="2")

    monkeypatch.setattr("services.neurocomment.list_comments_page", _fake)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/c1/comments", params={"limit": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"items", "next_cursor"}
    assert [c["post_id"] for c in body["items"]] == [2, 1]
    assert body["next_cursor"] == "2"


@pytest.mark.asyncio
async def test_list_comments_invalid_cursor_is_400(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(campaign_id: str, cursor: str | None, limit: int) -> Page[CommentRecord]:  # noqa: ARG001
        raise InvalidCursorError(cursor)

    monkeypatch.setattr("services.neurocomment.list_comments_page", _fake)
    async with _client(app) as client:
        resp = await client.get(
            "/api/v1/neurocomment/campaigns/c1/comments",
            params={"cursor": "nope"},
        )
    assert resp.status_code == 400
