"""Tests for the paginated published-comment history (repo + service).

Seeds real ``posted`` comment rows and asserts the newest-first page, offset
paging, ``posted``-only + campaign scoping (repo) and the cursor/``limit+1``
probe (service). Mirrors the board-test seed helpers and the logs pagination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import (
    assign_account_to_campaign,
    claim_comment,
    configure_database,
    create_account,
    create_campaign,
    link_channel_to_campaign,
    list_posted_comments_page,
    mark_comment_failed,
    mark_comment_posted,
)
from core.logging import reset_logging_for_tests, setup_logging
from schemas.accounts import AccountCreate
from schemas.neurocomment import CampaignCreate
from services.neurocomment import InvalidCursorError, list_comments_page
from services.neurocomment.comments_page import _decode_cursor

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()


async def _seed_campaign(campaign_id_name: str = "C1") -> str:
    campaign = await create_campaign(CampaignCreate(name=campaign_id_name, prompt="p"))
    await create_account(AccountCreate(account_id="acc-1", label="Account One"))
    await assign_account_to_campaign(campaign.campaign_id, "acc-1")
    await link_channel_to_campaign(campaign.campaign_id, "@chan")
    return campaign.campaign_id


async def _post(campaign_id: str, post_id: int, *, text: str = "hi") -> None:
    await claim_comment("@chan", post_id, campaign_id, "acc-1")
    await mark_comment_posted("@chan", post_id, comment_text=text, comment_msg_id=post_id)


@pytest.mark.asyncio
async def test_repo_orders_newest_first() -> None:
    campaign_id = await _seed_campaign()
    for post_id in (1, 2, 3):
        await _post(campaign_id, post_id, text=str(post_id))

    page = await list_posted_comments_page(campaign_id, offset=0, limit=50)

    # created_at ties within the same second → post_id desc breaks the tie.
    assert [c.post_id for c in page.comments] == [3, 2, 1]


@pytest.mark.asyncio
async def test_repo_limit_and_offset_paging() -> None:
    campaign_id = await _seed_campaign()
    for post_id in (1, 2, 3, 4, 5):
        await _post(campaign_id, post_id)

    first = await list_posted_comments_page(campaign_id, offset=0, limit=2)
    second = await list_posted_comments_page(campaign_id, offset=2, limit=2)

    assert [c.post_id for c in first.comments] == [5, 4]
    assert [c.post_id for c in second.comments] == [3, 2]


@pytest.mark.asyncio
async def test_repo_only_posted() -> None:
    campaign_id = await _seed_campaign()
    await _post(campaign_id, 1)
    await claim_comment("@chan", 2, campaign_id, "acc-1")  # left 'claimed'
    await claim_comment("@chan", 3, campaign_id, "acc-1")
    await mark_comment_failed("@chan", 3)

    page = await list_posted_comments_page(campaign_id, offset=0, limit=50)

    assert [c.post_id for c in page.comments] == [1]


@pytest.mark.asyncio
async def test_repo_campaign_scoped() -> None:
    mine = await _seed_campaign("Mine")
    other = await create_campaign(CampaignCreate(name="Other", prompt="p"))
    await assign_account_to_campaign(other.campaign_id, "acc-1")
    await link_channel_to_campaign(other.campaign_id, "@other")
    await _post(mine, 1)
    await claim_comment("@other", 9, other.campaign_id, "acc-1")
    await mark_comment_posted("@other", 9, comment_text="x", comment_msg_id=9)

    page = await list_posted_comments_page(mine, offset=0, limit=50)

    assert [c.post_id for c in page.comments] == [1]


@pytest.mark.asyncio
async def test_service_next_cursor_on_full_page() -> None:
    campaign_id = await _seed_campaign()
    for post_id in (1, 2, 3):
        await _post(campaign_id, post_id)

    page = await list_comments_page(campaign_id, cursor=None, limit=2)

    assert [c.post_id for c in page.items] == [3, 2]
    assert page.next_cursor == "2"


@pytest.mark.asyncio
async def test_service_last_page_has_no_cursor() -> None:
    campaign_id = await _seed_campaign()
    for post_id in (1, 2, 3):
        await _post(campaign_id, post_id)

    page = await list_comments_page(campaign_id, cursor="2", limit=2)

    assert [c.post_id for c in page.items] == [1]
    assert page.next_cursor is None


def test_decode_cursor_none_is_zero() -> None:
    assert _decode_cursor(None) == 0


@pytest.mark.parametrize("bad", ["nope", "-1"])
def test_decode_cursor_rejects_bad(bad: str) -> None:
    with pytest.raises(InvalidCursorError):
        _decode_cursor(bad)
