"""The key an autoreply checks is the key it answers with.

Its own file because ``test_autoreply.py`` is at the size limit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import save_warming_settings
from core.repositories import neuroshilling as repository
from schemas.gemini import GeminiResult
from schemas.neuroshilling import NeuroshillingChatMessage
from schemas.telegram_actions import PostComment
from services.neuroshilling import _autoreply, _seams
from services.neuroshilling._context import RunContext
from tests.services.neuroshilling.helpers import seed_campaign, sent

if TYPE_CHECKING:
    from schemas.gemini import GeminiRequest
    from schemas.telegram_actions import ActionResult, TelegramAction

_TARGET = "@alpha"
_CHATS = {"acc-1": 555}


class _Rng:
    def random(self) -> float:
        return 0.0

    def choice(self, values: list[str]) -> str:
        return values[0]


@pytest.mark.asyncio
async def test_a_key_cleared_after_the_check_still_answers_with_the_checked_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator clears the key between the check and the call.

    A re-read key was ``""``: the request failed validation and the claimed message
    was dropped.
    """
    keys: list[str] = []
    actions: list[TelegramAction] = []

    async def model(request: GeminiRequest) -> GeminiResult:
        keys.append(request.api_key)
        return GeminiResult(status="ok", text="да, беру уже полгода")

    async def execute(_account_id: str, action: TelegramAction) -> ActionResult:
        actions.append(action)
        return sent(777)

    original_pick = _autoreply._pick_account

    async def pick_then_clear(*args: object) -> str | None:
        await save_warming_settings(gemini_api_key=None, deepseek_api_key="")
        return await original_pick(*args)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(settings.deepseek, "api_key", "")  # no .env fallback to fall to
    await save_warming_settings(gemini_api_key=None, deepseek_api_key="sk-deepseek")
    monkeypatch.setattr(_seams, "generate_text_deepseek", model)
    monkeypatch.setattr(_seams, "execute", execute)
    monkeypatch.setattr(_seams, "rng", _Rng())
    monkeypatch.setattr(_autoreply, "_pick_account", pick_then_clear)
    seeded = await seed_campaign(targets=_TARGET, reply_to_humans=True, autoresponder="neurodialog")
    campaign = await repository.fetch_campaign(seeded.campaign_id)
    assert campaign is not None
    context = RunContext(
        campaign=campaign,
        run_id="run-1",
        steps=list(seeded.steps),
        by_position={},
        by_role={},
        halted=set(),
        banned={},
        banned_in={},
    )
    message = NeuroshillingChatMessage(message_id=900, text="а доставка быстрая?", is_ours=False)
    await repository.record_chat_messages(campaign.campaign_id, _TARGET, [message])

    await _autoreply.consider(context, _TARGET, _CHATS, message)

    assert keys == ["sk-deepseek"]
    assert len(actions) == 1
    assert isinstance(actions[0], PostComment)
