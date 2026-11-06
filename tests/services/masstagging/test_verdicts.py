"""How a refused story is read: the gateway's own stable codes, not Telethon's names."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from services.masstagging._verdicts import Verdict, classify
from tests.services.masstagging.fakes import answer

if TYPE_CHECKING:
    from schemas.telegram_actions import ActionResult


@pytest.mark.parametrize(
    ("code", "verdict"),
    [("premium_required", "premium"), ("stories_too_much", "too_much")],
)
def test_the_gateways_story_codes_are_recognised(code: str, verdict: Verdict) -> None:
    # The real shape (see ``test_scheduled_gateway``): one class, a lowercase stable code.
    result = answer("failed", error_type="ProfileGatewayError", error_message=code)

    assert classify(result) == verdict


@pytest.mark.parametrize(
    "result",
    [
        # Not the gateway's class: a third party's text is never a code.
        answer("failed", error_type="BadRequestError", error_message="stories_too_much"),
        answer("failed", error_type="BadRequestError", error_message="STORIES_TOO_MUCH"),
        answer("failed", error_type="ProfileGatewayError", error_message="STORIES_TOO_MUCH"),
        answer("failed", error_type="ProfileGatewayError", error_message="photo_too_small"),
    ],
)
def test_anything_else_is_an_ordinary_error(result: ActionResult) -> None:
    assert classify(result) == "error"


def test_a_raw_premium_class_name_still_means_premium() -> None:
    assert classify(answer("failed", error_type="PremiumAccountRequiredError")) == "premium"
