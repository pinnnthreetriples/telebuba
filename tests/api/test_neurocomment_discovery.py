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
    DiscoveryChannelVerdict,
    DiscoveryProgress,
    DiscoverySearchOutcome,
    DiscoverySearchRequest,
    DiscoverySourceReport,
)
from schemas.neurocomment_discovery_keywords import (
    DiscoveryKeywordRequest,
    DiscoveryKeywordResult,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

_BASE = "/api/v1/neurocomment/campaigns/c1/discovery"
# Not under ``_BASE``: expanding a topic needs no campaign, so it takes no id.
_KEYWORDS = "/api/v1/neurocomment/discovery/keywords"


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
                    source="telegram_similar",
                    state="skipped",
                    reason="seed_unusable",
                ),
            ],
        ),
        candidates=[
            DiscoveryCandidate(
                channel="cryptonews",
                title="Crypto News",
                subscribers=12345,
                source="telegram_search",
                sources=["telegram_search", "telegram_similar"],
                qualification="comments_on",
                verdict=DiscoveryChannelVerdict(
                    join_to_send=True,
                    group_slowmode_enabled=True,
                ),
            ),
            DiscoveryCandidate(
                channel="altcoins",
                title="Altcoins",
                source="telegram_similar",
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
            json={"keywords": ["crypto", "trading"]},
        )

    assert resp.status_code == 202
    assert resp.json() == {"status": "started"}
    assert seen[0].keywords == ["crypto", "trading"]


@pytest.mark.parametrize(
    "status",
    ["already_running", "no_account", "account_busy", "account_cooling", "daily_limit_reached"],
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
    """Ten copies of one keyword spent ten identical Telegram RPCs."""
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


@pytest.mark.asyncio
async def test_a_retired_catalogue_field_is_refused(app: FastAPI) -> None:
    """Nothing filters by locale or reads a catalogue any more.

    ``extra="forbid"`` is what makes an old client's request fail loudly instead of
    running unfiltered and reporting success. One field is the whole test: ``language``,
    ``country``, ``use_telemetr`` and ``catalogue_only`` all reach the same rejection,
    so a case each proved the same thing four times.
    """
    async with _client(app) as client:
        resp = await client.post(f"{_BASE}/search", json={"keywords": ["crypto"], "language": "tr"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_a_blank_seed_channel_is_rejected(app: FastAPI) -> None:
    """Truthy, so it survived into a pace sleep and a peer resolution and yielded nothing."""
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/search",
            json={"keywords": ["crypto"], "seed_channel": " "},
        )

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
    assert first["sources"] == ["telegram_search", "telegram_similar"]
    # The fitness verdict is what lets the board say WHY a channel is a dead end. An
    # unmeasured gate crosses the wire as null — unknown, never a cleared gate — and a
    # candidate with no verdict at all (run lost to a restart) carries null.
    assert first["verdict"]["join_to_send"] is True
    assert first["verdict"]["group_slowmode_enabled"] is True
    assert first["verdict"]["can_send_messages"] is None
    assert second["verdict"] is None
    assert second["qualification"] == "pending"
    assert second["taken_by_other_campaign"] is True
    # Per-source reporting is the only thing that tells the operator a source did not
    # answer: a single ``last_error`` collapsed every source into one first-error-wins code.
    reported = body["progress"]["sources"]
    assert [(item["source"], item["state"], item["kept"]) for item in reported] == [
        ("telegram_search", "ran", 1),
        ("telegram_similar", "skipped", 0),
    ]
    assert reported[1]["reason"] == "seed_unusable"


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
                DiscoveryAdoptOutcome(status="comments_off", channel="delta"),
                DiscoveryAdoptOutcome(status="failed", channel="gamma"),
            ],
        )

    monkeypatch.setattr("services.neurocomment.adopt_candidates", _fake)
    async with _client(app) as client:
        resp = await client.post(
            f"{_BASE}/adopt",
            json={"channels": ["alpha", "beta", "delta", "gamma"]},
        )

    # A channel whose link attempt raised is part of the batch's report, not a 500:
    # the ones that linked stay linked, so the operator has to be told which. Same for
    # ``comments_off`` — a server-side refusal, not a client mistake.
    assert resp.status_code == 200
    assert [item["status"] for item in resp.json()["outcomes"]] == [
        "linked",
        "already_assigned",
        "comments_off",
        "failed",
    ]
    assert seen == [["alpha", "beta", "delta", "gamma"]]


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
async def test_expand_keywords_returns_the_service_result(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not campaign-scoped: the path carries no id and the service is handed none.

    ``seen`` is asserted because the route's whole job is passing the typed body
    through — a route that ignored it and expanded a constant would still answer 200.
    """
    seen: list[DiscoveryKeywordRequest] = []

    async def _fake(body: DiscoveryKeywordRequest) -> DiscoveryKeywordResult:
        seen.append(body)
        return DiscoveryKeywordResult(keywords=["драки", "мордобой"])

    monkeypatch.setattr("services.neurocomment.expand_discovery_keywords", _fake)
    async with _client(app) as client:
        resp = await client.post(_KEYWORDS, json={"topic": "уличные драки"})

    assert resp.status_code == 200
    assert resp.json() == {"keywords": ["драки", "мордобой"], "error": None}
    assert [body.topic for body in seen] == ["уличные драки"]


@pytest.mark.asyncio
async def test_expand_keywords_reports_a_refusal_as_a_200_with_a_code(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unusable LLM is a locale-neutral code the SPA maps, not an HTTP error."""

    async def _fake(_body: DiscoveryKeywordRequest) -> DiscoveryKeywordResult:
        return DiscoveryKeywordResult(error="llm_unavailable")

    monkeypatch.setattr("services.neurocomment.expand_discovery_keywords", _fake)
    async with _client(app) as client:
        resp = await client.post(_KEYWORDS, json={"topic": "уличные драки"})

    assert resp.status_code == 200
    assert resp.json() == {"keywords": [], "error": "llm_unavailable"}


@pytest.mark.parametrize("body", [{}, {"topic": ""}, {"topic": "a" * (KEYWORD_MAX_LENGTH + 1)}])
@pytest.mark.asyncio
async def test_expand_keywords_rejects_an_unusable_topic(app: FastAPI, body: dict) -> None:
    """The topic is interpolated into a prompt, so its bounds are the route's job."""
    async with _client(app) as client:
        resp = await client.post(_KEYWORDS, json=body)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_expand_keywords_refuses_a_campaign_id_it_would_not_use(app: FastAPI) -> None:
    """``extra="forbid"``: a caller passing one must be told it changes nothing."""
    async with _client(app) as client:
        resp = await client.post(_KEYWORDS, json={"topic": "драки", "campaign_id": "c1"})

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_discovery_routes_require_authentication() -> None:
    """The sibling router inherits the parent router's auth dependency."""
    unauthenticated = create_app()
    async with _client(unauthenticated) as client:
        search = await client.post(f"{_BASE}/search", json={"keywords": ["crypto"]})
        board = await client.get(_BASE)
        adopt = await client.post(f"{_BASE}/adopt", json={"channels": ["alpha"]})
        keywords = await client.post(_KEYWORDS, json={"topic": "драки"})

    assert search.status_code == 401
    assert board.status_code == 401
    assert adopt.status_code == 401
    assert keywords.status_code == 401
