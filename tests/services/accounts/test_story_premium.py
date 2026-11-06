"""Premium policy for story designs, and how a post uses it."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.config import settings
from schemas.profile_media import AccountStoryUpload
from schemas.story_design import StoryDesign
from schemas.telegram_actions import ActionResult, PostStory
from services.accounts import AccountActionError, post_account_story
from services.accounts._story_premium import (
    check_design_limits,
    flat_design,
    premium_policy,
    refused_for_premium,
)

_FREE_MAX = 200
_STICKER = {"url": "example.com", "label": "Shop", "x": 50, "y": 50}


def _design(**fields: Any) -> StoryDesign:
    return StoryDesign.model_validate(fields)


@pytest.mark.parametrize(
    ("design", "lost"),
    [
        ({}, []),
        ({"period_hours": 24}, []),
        ({"period_hours": 6}, ["period"]),
        ({"period_hours": 12}, ["period"]),
        ({"period_hours": 48}, ["period"]),
        ({"link_stickers": [_STICKER]}, ["link_stickers"]),
        ({"caption": "x" * 200}, []),
        ({"caption": "x" * 201}, ["long_caption"]),
        (
            {"caption": "hello", "entities": [{"type": "bold", "offset": 0, "length": 5}]},
            ["formatting"],
        ),
        # Pin, no-forward and the audience are free for everyone.
        ({"pinned": True, "protect_content": True, "privacy_preset": "public"}, []),
        (
            {
                "period_hours": 48,
                "link_stickers": [_STICKER],
                "caption": "y" * 300,
                "entities": [{"type": "italic", "offset": 0, "length": 3}],
            },
            ["period", "link_stickers", "long_caption", "formatting"],
        ),
    ],
)
def test_the_policy_lists_exactly_the_premium_parts(
    design: dict[str, Any], lost: list[str]
) -> None:
    _, dropped = premium_policy(_design(**design), _FREE_MAX)

    assert dropped == lost


def test_a_clean_design_is_returned_untouched() -> None:
    design = _design(caption="hi", pinned=True)

    assert premium_policy(design, _FREE_MAX)[0] is design


def test_everything_premium_is_stripped_and_the_rest_is_kept() -> None:
    design = _design(
        caption="word " * 60,
        entities=[{"type": "bold", "offset": 0, "length": 4}],
        period_hours=48,
        link_stickers=[_STICKER],
        pinned=True,
        protect_content=True,
        privacy_preset="close_friends",
    )

    free, _ = premium_policy(design, _FREE_MAX)

    assert (free.period_hours, free.link_stickers, free.entities) == (24, [], [])
    assert (free.pinned, free.protect_content, free.privacy_preset) == (
        True,
        True,
        "close_friends",
    )
    assert free.caption is not None
    assert len(free.caption) <= _FREE_MAX
    # The caller's design is not mutated.
    assert design.period_hours == 48


def test_a_long_caption_is_cut_at_a_word_boundary_with_an_ellipsis() -> None:
    caption = "alpha beta gamma " * 20

    free, _ = premium_policy(_design(caption=caption), 20)

    assert free.caption == "alpha beta gamma…"
    assert caption.startswith(free.caption.removesuffix("…"))


def test_a_caption_without_spaces_is_cut_hard() -> None:
    free, _ = premium_policy(_design(caption="x" * 300), 20)

    assert free.caption == "x" * 19 + "…"


def test_a_cut_never_exceeds_the_limit_even_after_a_trailing_space() -> None:
    free, _ = premium_policy(_design(caption="ab " + "c" * 40), 10)

    assert free.caption is not None
    assert free.caption == "ab…"
    assert len(free.caption) <= 10


def test_the_free_limit_counts_utf16_units_like_telegram_and_the_spa() -> None:
    # 6 emoji = 6 code points but 12 UTF-16 units: over a 10-unit limit.
    _, lost = premium_policy(_design(caption="😀" * 6), 10)
    free, _ = premium_policy(_design(caption="😀" * 20), 10)

    assert "long_caption" in lost
    assert free.caption == "😀" * 4 + "…"
    assert len(free.caption.encode("utf-16-le")) // 2 <= 10


def test_flat_fields_become_the_nearest_offered_period() -> None:
    design = flat_design(
        caption="c", privacy_preset="public", protect_content=True, period_seconds=100_000
    )

    assert (design.caption, design.privacy_preset, design.protect_content) == ("c", "public", True)
    assert design.period_hours == 24
    assert design.entities == []


def test_the_configured_sticker_count_and_label_length_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_design_limits(_design(link_stickers=[_STICKER] * 3))

    monkeypatch.setattr(settings.profile_media, "story_link_stickers_max", 1)
    with pytest.raises(AccountActionError, match="story_design_invalid"):
        check_design_limits(_design(link_stickers=[_STICKER] * 2))

    monkeypatch.setattr(settings.profile_media, "story_link_stickers_max", 3)
    monkeypatch.setattr(settings.profile_media, "story_link_label_max", 3)
    with pytest.raises(AccountActionError, match="story_design_invalid"):
        check_design_limits(_design(link_stickers=[_STICKER]))


def _result(status: str = "ok", **fields: Any) -> ActionResult:
    return ActionResult.model_validate(
        {"status": status, "action_type": "post_story", "account_id": "a", **fields}
    )


def _premium_refusal() -> ActionResult:
    return _result("failed", error_type="ProfileGatewayError", error_message="premium_required")


def test_only_the_gateways_premium_code_counts_as_a_premium_refusal() -> None:
    assert refused_for_premium(_premium_refusal())
    assert not refused_for_premium(_result())
    assert not refused_for_premium(_result("failed", error_type="OtherError"))
    # A third party's English message is never a code.
    assert not refused_for_premium(
        _result("failed", error_type="RPCError", error_message="premium_required")
    )


class _Telegram:
    def __init__(self, *answers: ActionResult) -> None:
        self.answers = list(answers) or [_result()]
        self.sent: list[PostStory] = []

    async def execute(self, _account_id: str, action: PostStory) -> ActionResult:
        self.sent.append(action)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def _install(
    monkeypatch: pytest.MonkeyPatch, *answers: ActionResult, premium: bool | None
) -> _Telegram:
    telegram = _Telegram(*answers)
    monkeypatch.setattr("services.accounts.media.execute", telegram.execute)

    async def fetch(_account_id: str) -> object:
        return SimpleNamespace(premium=premium)

    monkeypatch.setattr("services.accounts.media.fetch_account", fetch)
    return telegram


def _upload(**design: Any) -> AccountStoryUpload:
    return AccountStoryUpload(
        account_id="acc",
        filename="s.jpg",
        content=b"jpg",
        media_kind="image",
        design=_design(**design),
    )


_FULL = {
    "caption": "word " * 60,
    "entities": [{"type": "bold", "offset": 0, "length": 4}],
    "period_hours": 48,
    "link_stickers": [_STICKER],
    "pinned": True,
}


@pytest.mark.asyncio
async def test_a_free_account_is_downgraded_before_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = _install(monkeypatch, premium=False)

    result = await post_account_story(_upload(**_FULL))

    assert len(telegram.sent) == 1
    sent = telegram.sent[0]
    assert (sent.period_seconds, sent.link_stickers, sent.entities) == (86_400, [], [])
    assert sent.pinned is True
    assert sent.caption is not None
    assert len(sent.caption) <= _FREE_MAX
    assert result.downgraded == ["period", "link_stickers", "long_caption", "formatting"]
    assert result.status == "ok"


@pytest.mark.asyncio
async def test_a_premium_account_sends_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = _install(monkeypatch, premium=True)

    result = await post_account_story(_upload(**_FULL))

    assert len(telegram.sent) == 1
    sent = telegram.sent[0]
    assert (sent.period_seconds, len(sent.link_stickers), len(sent.entities)) == (172_800, 1, 1)
    assert sent.caption == "word " * 60
    assert result.downgraded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("premium", [None, True])
async def test_a_premium_refusal_retries_once_downgraded(
    monkeypatch: pytest.MonkeyPatch,
    premium: bool | None,  # noqa: FBT001 - parametrized fixture value
) -> None:
    telegram = _install(monkeypatch, _premium_refusal(), _result(message_id=9), premium=premium)

    result = await post_account_story(_upload(**_FULL))

    assert [s.period_seconds for s in telegram.sent] == [172_800, 86_400]
    assert telegram.sent[1].link_stickers == []
    assert result.message_id == 9
    assert result.downgraded == ["period", "link_stickers", "long_caption", "formatting"]


@pytest.mark.asyncio
async def test_no_retry_when_the_downgrade_would_change_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _install(monkeypatch, _premium_refusal(), premium=None)

    with pytest.raises(AccountActionError, match="premium_required"):
        await post_account_story(_upload(caption="plain"))

    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_a_known_free_account_is_not_retried_after_a_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _install(monkeypatch, _premium_refusal(), premium=False)

    with pytest.raises(AccountActionError, match="premium_required"):
        await post_account_story(_upload(**_FULL))

    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_other_refusals_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    telegram = _install(
        monkeypatch,
        _result("failed", error_type="ProfileGatewayError", error_message="stories_too_much"),
        premium=None,
    )

    with pytest.raises(AccountActionError, match="stories_too_much"):
        await post_account_story(_upload(**_FULL))

    assert len(telegram.sent) == 1


@pytest.mark.asyncio
async def test_an_unknown_account_row_is_treated_as_unobserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _install(monkeypatch, premium=True)

    async def missing(_account_id: str) -> None:
        return None

    monkeypatch.setattr("services.accounts.media.fetch_account", missing)

    await post_account_story(_upload(**_FULL))

    assert telegram.sent[0].period_seconds == 172_800


@pytest.mark.asyncio
async def test_the_flat_fields_still_work_and_a_design_wins_over_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _install(monkeypatch, premium=True)

    await post_account_story(
        AccountStoryUpload(
            account_id="acc",
            filename="s.jpg",
            content=b"jpg",
            media_kind="image",
            caption="flat",
            privacy_preset="public",
            protect_content=True,
            period_seconds=172_800,
        )
    )
    await post_account_story(
        AccountStoryUpload(
            account_id="acc",
            filename="s.jpg",
            content=b"jpg",
            media_kind="image",
            caption="flat",
            design=_design(caption="designed", period_hours=6),
        )
    )

    flat, designed = telegram.sent
    assert (flat.caption, flat.privacy_preset, flat.protect_content) == ("flat", "public", True)
    assert flat.period_seconds == 172_800
    assert (designed.caption, designed.period_seconds, designed.privacy_preset) == (
        "designed",
        21_600,
        "contacts",
    )


@pytest.mark.asyncio
async def test_too_many_stickers_for_the_configured_limit_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram = _install(monkeypatch, premium=True)
    monkeypatch.setattr(settings.profile_media, "story_link_stickers_max", 1)

    with pytest.raises(AccountActionError, match="story_design_invalid"):
        await post_account_story(_upload(link_stickers=[_STICKER, _STICKER]))

    assert telegram.sent == []
