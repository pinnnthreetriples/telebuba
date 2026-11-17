"""The human sequence on the services side: the plan, the executor, the samples, the switch.

``core`` is faked: ``converse_spam_status`` stands in for the conversation, so what is tested is
that the gateway draws the pauses with ITS rng, that the executor asks the conversation only when
the switch is on, and that a transcript is classified, stored and remembered like a reply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import _get_engine, get_spam_status
from core.repositories.spam_reply_samples import list_reply_samples
from schemas.spam_probe import HumanProbePlan
from schemas.spam_status import SpamStatusProbe
from services import spam_intel, spam_status
from services.spam_intel import _human
from tests.services.spam_intel._reply_fixtures import FIXTURES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tests.services.spam_intel.conftest import FakeProbe

    MakeAccount = Callable[..., Awaitable[None]]

EN = FIXTURES["en"]
PLAN = HumanProbePlan(
    pre_open=1.0,
    open_to_typing=2.0,
    typing_seconds=1.0,
    reply_wait_max=30.0,
    quiet_after_last_reply=8.0,
    read_delay=1.0,
)


@dataclass
class FakeConversation:
    """Stands in for ``converse_spam_status``: records the plan, answers a scripted transcript."""

    texts: list[str] = field(default_factory=lambda: [EN.clean])
    has_markup: bool = False
    error: str | None = None
    plans: list[HumanProbePlan] = field(default_factory=list)

    async def __call__(self, account_id: str, plan: HumanProbePlan) -> SpamStatusProbe:
        self.plans.append(plan)
        if self.error is not None:
            return SpamStatusProbe(account_id=account_id, error=self.error)
        return SpamStatusProbe(
            account_id=account_id,
            reply_text="\n\n".join(self.texts) or None,
            texts=self.texts,
            has_markup=self.has_markup,
            n_messages=len(self.texts),
        )


@pytest.fixture
def conversation(monkeypatch: pytest.MonkeyPatch) -> FakeConversation:
    fake = FakeConversation()
    monkeypatch.setattr(spam_status, "converse_spam_status", fake)
    return fake


@pytest.fixture
def human_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.spam_intel, "human_sequence", True)


def test_both_new_switches_are_off_by_default() -> None:
    fresh = type(settings.spam_intel)()
    assert fresh.human_sequence is False
    assert fresh.live_session_embedding is False
    assert fresh.strict_parser is False  # untouched by this stage


def test_the_plan_is_drawn_from_the_configured_ranges() -> None:
    cfg = type(settings.spam_intel)(
        human_pre_open_min_seconds=1.0,
        human_pre_open_max_seconds=1.0,
        human_open_to_typing_min_seconds=2.0,
        human_open_to_typing_max_seconds=2.0,
        human_typing_min_seconds=3.0,
        human_typing_max_seconds=3.0,
        human_reply_wait_seconds=40.0,
        human_quiet_min_seconds=9.0,
        human_quiet_max_seconds=9.0,
        human_read_delay_min_seconds=4.0,
        human_read_delay_max_seconds=4.0,
    )

    class _Rng:
        def uniform(self, low: float, high: float) -> float:
            return (low + high) / 2

    assert _human.draw_plan(_Rng(), cfg) == HumanProbePlan(
        pre_open=1.0,
        open_to_typing=2.0,
        typing_seconds=3.0,
        reply_wait_max=40.0,
        quiet_after_last_reply=9.0,
        read_delay=4.0,
    )


def test_the_plan_stays_inside_the_default_ranges() -> None:
    import random  # noqa: PLC0415 - only this test draws from a real generator

    cfg = type(settings.spam_intel)()
    for _ in range(50):
        plan = _human.draw_plan(random.SystemRandom(), cfg)
        assert cfg.human_pre_open_min_seconds <= plan.pre_open <= cfg.human_pre_open_max_seconds
        assert cfg.human_quiet_min_seconds <= plan.quiet_after_last_reply
        assert plan.quiet_after_last_reply <= cfg.human_quiet_max_seconds
        assert plan.reply_wait_max == cfg.human_reply_wait_seconds


@pytest.mark.asyncio
async def test_switch_off_asks_the_single_probe_and_never_the_conversation(
    make_account: MakeAccount, probe: FakeProbe, conversation: FakeConversation
) -> None:
    """T16: ``human_sequence`` off reproduces the single ``/start`` probe exactly."""
    await make_account("acc-1")

    verdict = await spam_intel.ask("acc-1", force=True)

    assert (verdict.status, verdict.served_from) == ("clean", "probe")
    assert probe.calls == ["acc-1"]
    assert conversation.plans == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("human_on")
async def test_switch_on_asks_the_conversation_with_a_plan_from_the_gateways_rng(
    make_account: MakeAccount, probe: FakeProbe, conversation: FakeConversation
) -> None:
    await make_account("acc-1")

    verdict = await spam_intel.ask("acc-1", force=True)

    assert (verdict.status, verdict.served_from) == ("clean", "probe")
    assert probe.calls == []
    intel = settings.spam_intel
    # The test rng answers the low end of every range.
    assert conversation.plans == [
        HumanProbePlan(
            pre_open=intel.human_pre_open_min_seconds,
            open_to_typing=intel.human_open_to_typing_min_seconds,
            typing_seconds=intel.human_typing_min_seconds,
            reply_wait_max=intel.human_reply_wait_seconds,
            quiet_after_last_reply=intel.human_quiet_min_seconds,
            read_delay=intel.human_read_delay_min_seconds,
        )
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("human_on")
async def test_the_executor_without_a_plan_never_converses(
    make_account: MakeAccount,
    probe: FakeProbe,
    conversation: FakeConversation,
) -> None:
    """The pre-gateway funnel (``SPAM_INTEL__ENABLED=false``) keeps the old single probe."""
    await make_account("acc-1")

    outcome = await spam_status.probe_and_store("acc-1")

    assert outcome.verdict.status == "clean"
    assert probe.calls == ["acc-1"]
    assert conversation.plans == []


@pytest.mark.asyncio
async def test_the_executor_with_a_plan_converses(
    make_account: MakeAccount, probe: FakeProbe, conversation: FakeConversation
) -> None:
    await make_account("acc-1")

    outcome = await spam_status.probe_and_store("acc-1", plan=PLAN)

    assert outcome.verdict.status == "clean"
    assert conversation.plans == [PLAN]
    assert probe.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("texts", "status"),
    [
        ([EN.clean, "Tap the button to learn more."], "clean"),
        ([EN.clean, EN.limited_dated], "limited"),
        ([EN.being_checked], "unknown"),
        ([], "unknown"),
    ],
)
@pytest.mark.usefixtures("human_on")
async def test_a_transcript_is_classified_and_stored(
    make_account: MakeAccount,
    conversation: FakeConversation,
    texts: list[str],
    status: str,
) -> None:
    await make_account("acc-1")
    conversation.texts = texts

    verdict = await spam_intel.ask("acc-1", force=True)

    assert verdict.status == status
    stored = await get_spam_status("acc-1")
    assert stored is not None
    assert stored.status == status


@pytest.mark.asyncio
@pytest.mark.usefixtures("human_on")
async def test_an_ambiguous_transcript_is_unknown_with_its_reason(
    make_account: MakeAccount, conversation: FakeConversation
) -> None:
    await make_account("acc-1")
    conversation.texts = [EN.clean, EN.being_checked]

    verdict = await spam_intel.ask("acc-1", force=True)

    assert (verdict.status, verdict.detail) == ("unknown", "ambiguous_reply")


@pytest.mark.asyncio
@pytest.mark.usefixtures("human_on")
async def test_a_failed_conversation_is_an_error_like_a_failed_probe(
    make_account: MakeAccount, conversation: FakeConversation
) -> None:
    await make_account("acc-1")
    conversation.error = "TimeoutError"

    verdict = await spam_intel.ask("acc-1", force=True)

    assert (verdict.status, verdict.detail) == ("unknown", "TimeoutError")
    assert await get_spam_status("acc-1") is None  # never cached


@pytest.mark.asyncio
@pytest.mark.usefixtures("human_on")
async def test_each_message_is_kept_as_a_redacted_sample_with_the_structural_flags(
    make_account: MakeAccount, conversation: FakeConversation
) -> None:
    await make_account("acc-1")
    with _get_engine().begin() as connection:
        connection.exec_driver_sql(
            "UPDATE accounts SET first_name = 'Maria' WHERE account_id = 'acc-1'"
        )
    conversation.texts = ["Hello Maria, your id is 123456789.", "Tap below to learn more."]
    conversation.has_markup = True

    await spam_intel.ask("acc-1", force=True)

    samples = {sample.sample: sample for sample in await list_reply_samples()}
    assert set(samples) == {"Hello <name>, your id is <num>.", "Tap below to learn more."}
    assert all(sample.multi_message and sample.has_markup for sample in samples.values())
    assert all(sample.source == "probe" for sample in samples.values())


@pytest.mark.asyncio
@pytest.mark.usefixtures("human_on")
async def test_a_single_message_conversation_is_not_flagged_multi_message(
    make_account: MakeAccount, conversation: FakeConversation
) -> None:
    await make_account("acc-1")
    conversation.texts = [EN.clean]

    await spam_intel.ask("acc-1", force=True)

    (sample,) = await list_reply_samples()
    assert (sample.multi_message, sample.has_markup) == (False, False)


@pytest.mark.asyncio
async def test_the_old_probe_stores_samples_with_no_structural_flags(
    make_account: MakeAccount,
) -> None:
    await make_account("acc-1")

    await spam_intel.ask("acc-1", force=True)

    (sample,) = await list_reply_samples()
    assert (sample.multi_message, sample.has_markup) == (False, False)
