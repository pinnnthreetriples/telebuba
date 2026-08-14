"""Which unavailable account spends a post, and which one only postpones it.

``_admit_write`` refuses a write for three different reasons and used to raise one
class for all of them: the account row is gone, the account is inside a warming cycle,
and the account is caught between ``promoted_to_nc`` and ``nc_handed_off``. Only the
first is permanent, but ``_settle_revoked_dispatch`` settled all three the same way::

    if isinstance(exc, _seams.NeurocommentAccountUnavailableError):
        await mark_comment_failed(event.channel, event.post_id)
        return PipelineOutcome.TERMINAL

``mark_comment_failed`` leaves the ``(channel, post_id)`` row in place and
``claim_comment`` inserts with a conflict guard, so that surviving row was what stopped
every later attempt -- for good, over a condition that ends by itself and after a
refusal that reached no Telegram request at all. The sibling branch calls
``release_claim``, which deletes the row and answers ``RETRYABLE``.

Each transient test therefore runs twice: once under the refusal, and once after it
lifts. The second half is the whole claim -- a post is only really retryable if a later
attempt can still comment on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.db import fetch_comment
from schemas.neurocomment_pipeline import PipelineOutcome
from schemas.telegram_actions import ActionResult, NewPostEvent
from services.neurocomment import _seams, engine
from tests.services.neurocomment.engine_support import (
    _FixedRng,
    _GenStub,
    _make_campaign,
)

if TYPE_CHECKING:
    from schemas.telegram_actions import TelegramAction

pytestmark = pytest.mark.usefixtures("isolate_engine")


class _Gateway:
    """Stands in for the real gateway, so neither a send nor its absence goes unnoticed."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(
        self,
        account_id: str,
        action: TelegramAction,
        *,
        domain: str = "neurocomment",  # noqa: ARG002 - mirrors the real signature
    ) -> ActionResult:
        self.calls.append(action.action_type)
        return ActionResult(
            status="ok",
            action_type=action.action_type,
            account_id=account_id,
            message_id=555,
        )


def _patch_llm(monkeypatch: pytest.MonkeyPatch) -> _Gateway:
    """Everything except the real ``_seams.execute``, whose admission is under test."""
    gateway = _Gateway()
    monkeypatch.setattr(_seams, "_gateway_execute", gateway)
    monkeypatch.setattr(_seams, "rng", _FixedRng())
    monkeypatch.setattr(_seams, "generate_text", _GenStub("a nice comment").generate_text)
    return gateway


@pytest.mark.asyncio
async def test_the_same_post_is_commented_when_the_account_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: identical setup, admission simply passes.

    Without this the tests below would also pass if some unrelated gate were settling
    the post -- this one shows the pipeline reaches the gateway and posts, so the
    availability gate is the only thing that changes the outcome.
    """
    await _make_campaign("@chan", "acc-1")
    gateway = _patch_llm(monkeypatch)

    outcome = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=9, text="hi"))

    assert gateway.calls == ["comment_on_post"]
    assert outcome == PipelineOutcome.TERMINAL
    record = await fetch_comment("@chan", 9)
    assert record is not None
    assert record.status == "posted"


@pytest.mark.asyncio
async def test_a_post_arriving_while_the_account_warms_is_commented_after_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The account is in warming for a moment; the post waits rather than being spent.

    ``_account_is_available`` reports False for any account id in
    ``list_warming_account_ids()``, and a warming cycle ends. So the claim is deleted
    and the outcome is ``RETRYABLE``, which is what puts the post back on the durable
    inbox's retry ladder instead of leaving a ``failed`` row no later attempt can win.
    """
    await _make_campaign("@chan", "acc-1")
    gateway = _patch_llm(monkeypatch)

    async def in_warming() -> list[str]:
        return ["acc-1"]

    monkeypatch.setattr(_seams, "list_warming_account_ids", in_warming)

    outcome = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    # Nothing was sent, so nothing is ambiguous -- and nothing is settled either.
    assert gateway.calls == []
    assert outcome == PipelineOutcome.RETRYABLE
    assert await fetch_comment("@chan", 10) is None

    async def none_warming() -> list[str]:
        return []

    monkeypatch.setattr(_seams, "list_warming_account_ids", none_warming)

    retry = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=10, text="hi"))

    assert gateway.calls == ["comment_on_post"]
    assert retry == PipelineOutcome.TERMINAL
    posted = await fetch_comment("@chan", 10)
    assert posted is not None
    assert posted.status == "posted"


@pytest.mark.asyncio
async def test_a_post_arriving_mid_handoff_is_commented_once_the_handoff_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same reprieve for an account that is not handed off *yet*.

    ``_account_is_handed_to_neurocomment`` requires both ``promoted_to_nc`` and
    ``nc_handed_off``. Between those two writes the account fails admission, and that
    window closes on its own within one handoff.
    """
    await _make_campaign("@chan", "acc-1")
    gateway = _patch_llm(monkeypatch)
    handed_off = False
    real_fetch_warming_state = _seams.fetch_warming_state

    async def mid_handoff(account_id: str) -> object:
        # No warming row yet: promoted_to_nc / nc_handed_off are unreadable.
        return await real_fetch_warming_state(account_id) if handed_off else None

    monkeypatch.setattr(_seams, "fetch_warming_state", mid_handoff)

    outcome = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=11, text="hi"))

    assert gateway.calls == []
    assert outcome == PipelineOutcome.RETRYABLE
    assert await fetch_comment("@chan", 11) is None

    handed_off = True

    retry = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=11, text="hi"))

    assert gateway.calls == ["comment_on_post"]
    assert retry == PipelineOutcome.TERMINAL
    posted = await fetch_comment("@chan", 11)
    assert posted is not None
    assert posted.status == "posted"


@pytest.mark.asyncio
async def test_a_post_whose_account_was_deleted_is_settled_for_good(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one permanent refusal keeps its terminal settlement.

    The account is picked from the campaign and then its row disappears before the
    write is admitted. Nothing brings it back, so retrying would only burn the inbox's
    attempt budget on a post that can never be commented: the ``failed`` row stands,
    and it is what stops any later claim.
    """
    await _make_campaign("@chan", "acc-1")
    gateway = _patch_llm(monkeypatch)

    async def deleted(_account_id: str) -> None:
        return None

    monkeypatch.setattr(_seams, "fetch_account", deleted)

    outcome = await engine.handle_new_post(NewPostEvent(channel="@chan", post_id=12, text="hi"))

    assert gateway.calls == []
    assert outcome == PipelineOutcome.TERMINAL
    record = await fetch_comment("@chan", 12)
    assert record is not None
    assert record.status == "failed"
