"""Channel-discovery endpoint tests — thin routes over a mocked service.

A separate module from ``test_neurocomment.py``, which is already close to the
700-line test cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from api import create_app
from schemas.neurocomment_discovery import (
    CHANNEL_HANDLE_MAX_LENGTH,
    KEYWORD_MAX_LENGTH,
    DiscoveryAdoptOutcome,
    DiscoveryAdoptResult,
    DiscoveryBoard,
    DiscoveryCandidate,
    DiscoveryProgress,
    DiscoverySearchOutcome,
    DiscoverySearchRequest,
    DiscoverySourceReport,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_BASE = "/api/v1/neurocomment/campaigns/c1/discovery"


def _client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def _board() -> DiscoveryBoard:
    return DiscoveryBoard(
        campaign_id="c1",
        progress=DiscoveryProgress(
            phase="qualifying",
            running=True,
            total=2,
            qualified=1,
            comments_on=1,
            last_error=None,
            sources=[
                DiscoverySourceReport(source="telegram_search", state="ran", hits=5, kept=1),
                DiscoverySourceReport(
                    source="telemetr",
                    state="failed",
                    reason="telemetr_quota_exhausted",
                    detail="HTTP 426: quota reached",
                ),
            ],
        ),
        candidates=[
            DiscoveryCandidate(
                channel="cryptonews",
                title="Crypto News",
                subscribers=12345,
                source="telegram_search",
                sources=["telegram_search", "telemetr"],
                country="TR",
                language="tr",
                qualification="comments_on",
            ),
            DiscoveryCandidate(
                channel="altcoins",
                title="Altcoins",
                source="telemetr",
                qualification="pending",
                taken_by_other_campaign=True,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_start_discovery_returns_202_and_the_outcome(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[DiscoverySearchRequest] = []

    async def _fake(campaign_id: str, body: DiscoverySearchRequest) -> DiscoverySearchOutcome:
        assert campaign_id == "c1"
        seen.append(body)
        return DiscoverySearchOutcome(status="started")

    monkeypatch.setattr("services.neurocomment.start_discovery", _fake)
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": ["crypto", "trading"], "use_telemetr": True},
        )

    assert resp.status_code == 202
    assert resp.json() == {"status": "started"}
    assert seen[0].keywords == ["crypto", "trading"]
    assert seen[0].use_telemetr is True


@pytest.mark.parametrize(
    "status",
    ["already_running", "no_account", "account_cooling", "daily_limit_reached"],
)
@pytest.mark.asyncio
async def test_refusals_ride_the_outcome_not_an_error(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
) -> None:
    """None of these is a client mistake, so none of them is an HTTP error."""

    async def _fake(campaign_id: str, body: DiscoverySearchRequest) -> DiscoverySearchOutcome:  # noqa: ARG001
        return DiscoverySearchOutcome.model_validate({"status": status})

    monkeypatch.setattr("services.neurocomment.start_discovery", _fake)
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/search", json={"keywords": ["crypto"]})

    assert resp.status_code == 202
    assert resp.json()["status"] == status


@pytest.mark.asyncio
async def test_short_keyword_is_rejected(app: FastAPI) -> None:
    """Telegram rejects global searches under 4 characters."""
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/search", json={"keywords": ["abc"]})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_empty_keywords_is_rejected(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/search", json={"keywords": []})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_too_many_keywords_is_rejected(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": [f"keyword{index}" for index in range(11)]},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_an_oversized_keyword_is_rejected(app: FastAPI) -> None:
    """The bounds validator measures the stripped form, so the raw item needs its own cap.

    Without it a 10 MB keyword passed validation and rode into a Telegram RPC.
    """
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": ["a" * (KEYWORD_MAX_LENGTH + 1)]},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_duplicate_keywords_collapse_to_one(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ten copies of one keyword spent ten Telegram RPCs and ten catalogue requests."""
    seen: list[DiscoverySearchRequest] = []

    async def _fake(campaign_id: str, body: DiscoverySearchRequest) -> DiscoverySearchOutcome:  # noqa: ARG001
        seen.append(body)
        return DiscoverySearchOutcome(status="started")

    monkeypatch.setattr("services.neurocomment.start_discovery", _fake)
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": [" Crypto ", "crypto", "CRYPTO", "trading"]},
        )

    assert resp.status_code == 202
    assert seen[0].keywords == ["Crypto", "trading"]


@pytest.mark.parametrize(
    "body",
    [
        {"keywords": ["crypto"], "language": "tr"},
        {"keywords": ["crypto"], "country": "TR"},
    ],
)
@pytest.mark.asyncio
async def test_filters_without_telemetr_are_rejected(app: FastAPI, body: dict[str, object]) -> None:
    """Only the catalogue applies them, so 202 here promised a filter that reached nothing."""
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/search", json=body)

    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"keywords": ["crypto"], "use_telemetr": True, "language": "klingon"},
        {"keywords": ["crypto"], "use_telemetr": True, "country": "tr"},
        {"keywords": ["crypto"], "seed_channel": " "},
    ],
)
@pytest.mark.asyncio
async def test_unusable_filter_values_are_rejected(app: FastAPI, body: dict[str, object]) -> None:
    """Validated against the codes the UI offers: anything else reached nothing, quietly.

    A blank seed is the same class of defect — truthy, so it survived into a pace sleep
    and a peer resolution and yielded nothing.
    """
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/search", json=body)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_inverted_member_bounds_are_rejected(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": ["crypto"], "members_min": 500, "members_max": 100},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_field_is_rejected(app: FastAPI) -> None:
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": ["crypto"], "surprise": 1},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_discovery_serializes_the_board(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake(campaign_id: str) -> DiscoveryBoard:  # noqa: ARG001
        return _board()

    monkeypatch.setattr("services.neurocomment.load_discovery", _fake)
    async with _client(app) as client:
        resp = await client.get(_BASE)

    assert resp.status_code == 200
    body = resp.json()
    assert body["progress"]["phase"] == "qualifying"
    assert body["progress"]["running"] is True
    assert body["progress"]["comments_on"] == 1
    first, second = body["candidates"]
    assert first["channel"] == "cryptonews"
    assert first["qualification"] == "comments_on"
    assert first["subscribers"] == 12345
    assert first["sources"] == ["telegram_search", "telemetr"]
    assert (first["country"], first["language"]) == ("TR", "tr")
    assert second["qualification"] == "pending"
    assert second["taken_by_other_campaign"] is True
    # Per-source reporting is the only thing that tells the operator a filter did not
    # apply: a single ``last_error`` collapsed every source into one first-error-wins code.
    reported = body["progress"]["sources"]
    assert [(item["source"], item["state"], item["kept"]) for item in reported] == [
        ("telegram_search", "ran", 1),
        ("telemetr", "failed", 0),
    ]
    assert reported[1]["detail"] == "HTTP 426: quota reached"


@pytest.mark.asyncio
async def test_get_discovery_unknown_campaign_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _none(campaign_id: str) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.load_discovery", _none)
    async with _client(app) as client:
        resp = await client.get("/api/v1/neurocomment/campaigns/ghost/discovery")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_adopt_returns_one_outcome_per_channel(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    async def _fake(campaign_id: str, channels: list[str]) -> DiscoveryAdoptResult:
        assert campaign_id == "c1"
        seen.append(channels)
        return DiscoveryAdoptResult(
            outcomes=[
                DiscoveryAdoptOutcome(status="linked", channel="alpha"),
                DiscoveryAdoptOutcome(status="already_assigned", channel="beta"),
                DiscoveryAdoptOutcome(status="failed", channel="gamma"),
            ],
        )

    monkeypatch.setattr("services.neurocomment.adopt_candidates", _fake)
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/adopt",
            json={"channels": ["alpha", "beta", "gamma"]},
        )

    # A channel whose link attempt raised is part of the batch's report, not a 500:
    # the ones that linked stay linked, so the operator has to be told which.
    assert resp.status_code == 200
    assert [item["status"] for item in resp.json()["outcomes"]] == [
        "linked",
        "already_assigned",
        "failed",
    ]
    assert seen == [["alpha", "beta", "gamma"]]


@pytest.mark.asyncio
async def test_adopt_unknown_campaign_is_404(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _none(campaign_id: str, channels: list[str]) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr("services.neurocomment.adopt_candidates", _none)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/ghost/discovery/adopt",
            json={"channels": ["alpha"]},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_adopt_rejects_an_empty_or_oversized_batch(app: FastAPI) -> None:
    async with _client(app) as client:
        empty = await client.post(f"{_BASE}/adopt", json={"channels": []})
        oversized = await client.post(
            f"{_BASE}/adopt",
            json={"channels": [f"chan_{index}" for index in range(501)]},
        )

    assert empty.status_code == 422
    assert oversized.status_code == 422


@pytest.mark.parametrize(
    "handle",
    ["", " ", "\t", " alpha", "a" * (CHANNEL_HANDLE_MAX_LENGTH + 1)],
)
@pytest.mark.asyncio
async def test_adopt_rejects_an_unusable_handle(app: FastAPI, handle: str) -> None:
    """Adopt writes the handle verbatim, so an unusable one must not get that far.

    A blank one 500'd inside the link transaction; a padded or over-long one persisted a
    campaign-channel row matching no candidate and no linked group. The plain
    channel-link route still takes any non-empty string — pre-existing, not this route's
    to fix, which is why these bounds live on the discovery request.
    """
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/adopt", json={"channels": ["alpha", handle]})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_404s_for_an_unknown_campaign(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account resolution is fleet-wide, so it cannot stand in for this check."""

    async def _none(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("services.neurocomment.start_discovery", _none)
    async with _client(app) as client:
        resp = await client.post(
            "/api/v1/neurocomment/campaigns/ghost/discovery/search",
            json={"keywords": ["crypto"]},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_discovery_routes_require_authentication() -> None:
    """The sibling router inherits the parent router's auth dependency."""
    unauthenticated = create_app()
    async with _client(unauthenticated) as client:
        search = await client.post(f"{_BASE}/search", json={"keywords": ["crypto"]})
        board = await client.get(_BASE)
        adopt = await client.post(f"{_BASE}/adopt", json={"channels": ["alpha"]})

    assert search.status_code == 401
    assert board.status_code == 401
    assert adopt.status_code == 401
