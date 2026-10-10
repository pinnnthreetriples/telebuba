"""Start a run against the fake and wait for it to settle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.repositories import user_parser as repository
from schemas.user_parser import UserParserRequest
from services.user_parser import _state, start_run

if TYPE_CHECKING:
    from schemas.user_parser_run import UserParserRun, UserParserUser


def parser_request(**overrides: Any) -> UserParserRequest:
    """A run with no pauses worth speaking of and no filter but the defaults."""
    values: dict[str, Any] = {
        "mode": "members",
        "sources": ["@group"],
        "account_ids": ["a1"],
        "protect": False,
        "chat_delay": 2,
        "request_delay": 1,
        "toggles": {"exclude_admins": False},
    }
    return UserParserRequest.model_validate(values | overrides)


async def run_to_end(request: UserParserRequest) -> UserParserRun:
    outcome = await start_run(request)
    assert outcome.status == "started", outcome
    assert outcome.run_id is not None
    await settled(outcome.run_id)
    stored = await repository.fetch_run(outcome.run_id)
    assert stored is not None
    return stored


async def settled(run_id: str) -> None:
    """Wait for the run's task; a fast one may already be done by the time start returns."""
    entry = _state.live(run_id)
    if entry is not None and entry.task is not None:
        await entry.task


async def kept(run_id: str) -> list[UserParserUser]:
    page = await repository.page_users(run_id, search="", offset=0, limit=500)
    return sorted(page.items, key=lambda user: user.user_id)
