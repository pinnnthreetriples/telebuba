"""The send journal: the conflict guarantee, the counters, and the boot sweep."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from core.repositories.neuroshilling import (
    claim_message,
    count_messages_since,
    count_sent_message_steps,
    create_campaign,
    fail_pending_messages,
    fetch_message_id,
    hand_over_message,
    list_journalled_steps,
    list_sent_message_ids,
    read_quota_usage,
    replace_scenario,
    settle_message,
)
from schemas.neuroshilling import (
    NeuroshillingCampaignCreate,
    NeuroshillingMessageStatus,
    NeuroshillingStepKey,
)
from schemas.neuroshilling_scenario import NeuroshillingRoleInput, NeuroshillingStepInput

if TYPE_CHECKING:
    from schemas.neuroshilling_scenario import NeuroshillingStep

_PAST = "1970-01-01T00:00:00+00:00"
_FUTURE = "2999-01-01T00:00:00+00:00"
_RUN = "run-1"
# Typed rather than a bare list of strings, so the parametrised call type-checks
# against ``settle_message``'s literal instead of being silenced at the call site.
_NOT_FAILED: list[NeuroshillingMessageStatus] = ["pending", "sent", "skipped"]


def _key(target: str, step_id: str) -> NeuroshillingStepKey:
    return NeuroshillingStepKey(run_id=_RUN, target=target, step_id=step_id)


async def _claim(campaign_id: str, target: str, step_id: str, account_id: str, text: str) -> bool:
    return await claim_message(
        _key(target, step_id),
        campaign_id=campaign_id,
        account_id=account_id,
        text=text,
    )


async def _campaign(*, reactions: int = 0) -> tuple[str, list[NeuroshillingStep]]:
    created = await create_campaign(NeuroshillingCampaignCreate(name="Promo"))
    steps = [
        NeuroshillingStepInput(role_id="#0", text="a"),
        NeuroshillingStepInput(role_id="#0", text="b"),
        *(
            NeuroshillingStepInput(role_id="#0", kind="reaction", emoji="🔥", target_position=1)
            for _ in range(reactions)
        ),
    ]
    await replace_scenario(created.campaign_id, [NeuroshillingRoleInput(name="R")], steps)
    from core.repositories.neuroshilling import load_scenario  # noqa: PLC0415 - one use.

    _roles, stored = await load_scenario(created.campaign_id)
    return created.campaign_id, stored


@pytest.mark.asyncio
async def test_a_second_claim_of_the_same_step_loses() -> None:
    """The idempotency the resume and the restart both rest on."""
    campaign_id, steps = await _campaign()
    first = await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")

    second = await _claim(campaign_id, "alpha", steps[0].step_id, "acc-2", "a")

    assert (first, second) == (True, False)


@pytest.mark.asyncio
async def test_the_same_step_in_another_target_is_a_different_key() -> None:
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")

    assert await _claim(campaign_id, "beta", steps[0].step_id, "acc-1", "a")


@pytest.mark.asyncio
async def test_a_message_id_is_only_readable_once_the_row_is_sent() -> None:
    """A ``pending`` row is a claim, not an answer: nothing may reply to it."""
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")
    assert await fetch_message_id(_key("alpha", steps[0].step_id)) is None

    await settle_message(_key("alpha", steps[0].step_id), status="sent", message_id=42)

    assert await fetch_message_id(_key("alpha", steps[0].step_id)) == 42


@pytest.mark.asyncio
async def test_a_settle_after_the_boot_sweep_does_not_resurrect_the_row() -> None:
    """The sweep has already decided; a late answer must not re-open its verdict."""
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")
    await fail_pending_messages(_RUN)

    settled = await settle_message(_key("alpha", steps[0].step_id), status="sent", message_id=7)

    assert settled is False
    assert await fetch_message_id(_key("alpha", steps[0].step_id)) is None


@pytest.mark.asyncio
async def test_the_boot_sweep_keeps_the_row_so_the_key_stays_taken() -> None:
    """Deleting it would free the key and let the resumed run send the step again."""
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")

    swept = await fail_pending_messages(_RUN)

    assert swept == 1
    assert ("alpha", steps[0].step_id) in await list_journalled_steps(_RUN)
    assert await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a") is False


@pytest.mark.asyncio
async def test_a_failed_row_is_handed_to_the_substitute_over_its_own_key() -> None:
    """The substitution's whole storage move: one UPDATE, no second key.

    A fresh insert would collide with the row the banned account left, and a delete
    followed by an insert would open a window in which the step looks unplayed.
    """
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")
    await settle_message(_key("alpha", steps[0].step_id), status="failed", error_type="Banned")

    assert await hand_over_message(_key("alpha", steps[0].step_id), account_id="res-1")

    assert await settle_message(_key("alpha", steps[0].step_id), status="sent", message_id=9)
    assert await fetch_message_id(_key("alpha", steps[0].step_id)) == 9
    assert await list_journalled_steps(_RUN) == {("alpha", steps[0].step_id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _NOT_FAILED)
async def test_only_a_failed_row_may_be_handed_over(status: NeuroshillingMessageStatus) -> None:
    """``pending`` may still be in flight, ``sent`` is published, ``skipped`` was refused."""
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")
    if status != "pending":
        await settle_message(_key("alpha", steps[0].step_id), status=status)

    assert await hand_over_message(_key("alpha", steps[0].step_id), account_id="res-1") is False


@pytest.mark.asyncio
async def test_a_pending_row_counts_against_the_quota() -> None:
    """The whole reason the row goes in first: an in-flight send has to be visible."""
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")

    usage = await read_quota_usage(
        campaign_id,
        "acc-1",
        "alpha",
        hour_since=_PAST,
        day_since=_PAST,
    )

    assert (usage.hour, usage.chat_day, usage.campaign_total) == (1, 1, 1)


@pytest.mark.asyncio
async def test_a_skipped_row_spends_nothing() -> None:
    campaign_id, steps = await _campaign()
    await claim_message(
        _key("alpha", steps[0].step_id),
        campaign_id=campaign_id,
        account_id="acc-1",
        text="a",
        status="skipped",
    )

    usage = await read_quota_usage(
        campaign_id,
        "acc-1",
        "alpha",
        hour_since=_PAST,
        day_since=_PAST,
    )

    assert (usage.hour, usage.chat_day, usage.campaign_total) == (0, 0, 0)


@pytest.mark.asyncio
async def test_a_reaction_is_not_counted_as_a_message() -> None:
    """The operator's field says "messages per hour", so a reaction must not spend one."""
    campaign_id, steps = await _campaign(reactions=1)
    await _claim(campaign_id, "alpha", steps[2].step_id, "acc-1", "")

    usage = await read_quota_usage(
        campaign_id,
        "acc-1",
        "alpha",
        hour_since=_PAST,
        day_since=_PAST,
    )

    assert usage.hour == 0


@pytest.mark.asyncio
async def test_the_hour_window_excludes_older_rows() -> None:
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")

    usage = await read_quota_usage(
        campaign_id,
        "acc-1",
        "alpha",
        hour_since=_FUTURE,
        day_since=_FUTURE,
    )

    assert (usage.hour, usage.chat_day) == (0, 0)
    # The lifetime count has no window and still sees it.
    assert usage.campaign_total == 1


@pytest.mark.asyncio
async def test_the_load_signal_is_one_grouped_read() -> None:
    campaign_id, steps = await _campaign()
    await _claim(campaign_id, "alpha", steps[0].step_id, "acc-1", "a")
    await _claim(campaign_id, "alpha", steps[1].step_id, "acc-1", "b")
    await _claim(campaign_id, "beta", steps[0].step_id, "acc-2", "a")

    assert await count_messages_since(["acc-1", "acc-2", "acc-3"], _PAST) == {
        "acc-1": 2,
        "acc-2": 1,
    }
    assert await count_messages_since([], _PAST) == {}


@pytest.mark.asyncio
async def test_progress_counts_delivered_messages_and_not_reactions() -> None:
    campaign_id, steps = await _campaign(reactions=1)
    for step in (steps[0], steps[2]):
        await _claim(campaign_id, "alpha", step.step_id, "acc-1", "a")
        await settle_message(_key("alpha", step.step_id), status="sent", message_id=1)
    await _claim(campaign_id, "alpha", steps[1].step_id, "acc-1", "b")

    assert await count_sent_message_steps(_RUN) == 1


@pytest.mark.asyncio
async def test_a_revive_cycles_rows_belong_to_the_run_that_spawned_them() -> None:
    """``run_scope`` folds ``{run_id}#{n}`` in, and both run-wide questions need it.

    A revive campaign replays the same dialogue for ever, so each cycle journals
    under a key of its own — otherwise the unique index turns the second cycle into
    a silent no-op. The progress counter then has to keep climbing across cycles,
    and the boot sweep has to settle a cycle's interrupted rows: an unswept
    ``pending`` row goes on consuming the account's quota for good.
    """
    campaign_id, steps = await _campaign()
    cycle = NeuroshillingStepKey(run_id=f"{_RUN}#2", target="alpha", step_id=steps[0].step_id)
    await claim_message(cycle, campaign_id=campaign_id, account_id="acc-1", text="a")
    await settle_message(cycle, status="sent", message_id=9)
    await _claim(campaign_id, "alpha", steps[1].step_id, "acc-1", "b")

    assert await count_sent_message_steps(_RUN) == 1
    assert await fail_pending_messages(_RUN) == 1
    # The plain key is not a prefix of another run's, so nothing else is swept.
    assert await fail_pending_messages("run") == 0


@pytest.mark.asyncio
async def test_our_own_message_ids_are_read_back_across_every_run_and_campaign() -> None:
    """What the chat poller answers "is this ours?" with.

    Telethon's ``out`` flag only covers the account doing the reading, so a line said
    by any other account looks like a stranger's without this. Not scoped to the
    current run, because an earlier run's messages are still in that chat — and not to
    the campaign either: a second campaign aimed at the same group read the first one's
    scripted lines as a stranger's and answered them. The TARGET is still the key: ids
    are only unique inside one chat.
    """
    campaign_id, steps = await _campaign()
    for run_id, step in ((_RUN, steps[0]), (f"{_RUN}#2", steps[1])):
        key = NeuroshillingStepKey(run_id=run_id, target="alpha", step_id=step.step_id)
        await claim_message(key, campaign_id=campaign_id, account_id="acc-1", text="a")
        await settle_message(key, status="sent", message_id=100 + step.position)
    other_campaign, other_steps = await _campaign()
    elsewhere = NeuroshillingStepKey(run_id="run-2", target="alpha", step_id=other_steps[0].step_id)
    await claim_message(elsewhere, campaign_id=other_campaign, account_id="acc-2", text="a")
    await settle_message(elsewhere, status="sent", message_id=205)
    # A claimed but unsettled row has no id in the chat, so it is not one of ours.
    await _claim(campaign_id, "beta", steps[0].step_id, "acc-1", "a")

    assert await list_sent_message_ids("alpha") == {101, 102, 205}
    assert await list_sent_message_ids("beta") == set()
