"""Tests for the neurocomment page's pure-display label maps (issue #119).

The page itself is ``pragma: no cover`` (UI-thin, exercised manually); these two
translation helpers carry the only branchy logic, so they get a unit test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from features.neurocomment import register_neurocomment_page
from features.neurocomment._page import (
    campaign_options,
    campaign_status_label,
    challenge_summary,
    channel_status_icon,
    channel_status_label,
    counter_window_since,
    health_label,
    solver_switch_key,
)
from schemas.challenge import ChallengeRow
from schemas.neurocomment import CampaignList, CampaignStatus, NeurocommentCampaign


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ready", "Готов"),
        ("comments_off", "Комментарии выключены"),
        ("join_by_request", "Вступление по заявке"),
        ("chat_restricted", "Блок записи (Telegram)"),
        ("bot_challenge", "Капча бота"),
        ("bot_challenge_backoff", "Капча бота · пауза"),
        ("throttled", "Лимит исчерпан"),
    ],
)
def test_channel_status_label_known(status: str, expected: str) -> None:
    assert channel_status_label(status) == expected


def test_channel_status_label_unknown_falls_back() -> None:
    assert channel_status_label("weird") == "weird"


def test_channel_status_split_states_have_distinct_icons() -> None:
    # Ф2 #120: the three split states must render as three visually-distinct badges.
    icons = [
        channel_status_icon("chat_restricted"),
        channel_status_icon("bot_challenge"),
        channel_status_icon("bot_challenge_backoff"),
    ]
    assert all(icons)
    assert len(set(icons)) == len(icons)


def test_channel_status_icon_unknown_falls_back_to_generic() -> None:
    assert channel_status_icon("weird") == "help_outline"


def _challenge_row(*, raw_text: str, button_labels: list[str]) -> ChallengeRow:
    return ChallengeRow(
        account_id="acc-1",
        channel="@chan",
        raw_text=raw_text,
        button_labels=button_labels,
        outcome="give_up",
        decided_at="2026-06-24T00:00:00Z",
    )


def test_challenge_summary_includes_text_and_buttons() -> None:
    summary = challenge_summary(_challenge_row(raw_text="2+2=?", button_labels=["4", "5"]))
    assert "2+2=?" in summary
    assert "4 · 5" in summary


def test_challenge_summary_handles_empty_text_and_buttons() -> None:
    summary = challenge_summary(_challenge_row(raw_text="   ", button_labels=[]))
    assert "(без текста)" in summary
    assert "—" in summary


def test_challenge_summary_appends_reasoning() -> None:
    row = ChallengeRow(
        account_id="a",
        channel="@c",
        raw_text="2+2=?",
        button_labels=["4"],
        outcome="failed",
        decided_at="t",
        reasoning="the math answer is 4",
    )
    assert "the math answer is 4" in challenge_summary(row)


def test_counter_window_since_today_is_start_of_day() -> None:
    now = datetime(2026, 6, 24, 15, 30, tzinfo=UTC)
    assert counter_window_since("today", now) == datetime(2026, 6, 24, tzinfo=UTC).isoformat()


def test_counter_window_since_7d_is_a_week_back() -> None:
    now = datetime(2026, 6, 24, tzinfo=UTC)
    assert counter_window_since("7d", now) == datetime(2026, 6, 17, tzinfo=UTC).isoformat()


def test_counter_window_since_all_is_empty() -> None:
    assert counter_window_since("all", datetime(2026, 6, 24, tzinfo=UTC)) == ""


@pytest.mark.parametrize(("value", "expected"), [(None, "follow"), (True, "on"), (False, "off")])
def test_solver_switch_key(value: bool | None, expected: str) -> None:  # noqa: FBT001 - parametrized value
    assert solver_switch_key(value) == expected


@pytest.mark.parametrize(("health", "expected"), [("ready", "Готов"), ("blocked", "Заблокирован")])
def test_health_label_known(health: str, expected: str) -> None:
    assert health_label(health) == expected


def test_health_label_unknown_falls_back() -> None:
    assert health_label("nope") == "nope"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("active", "Активна"), ("paused", "На паузе"), ("archived", "В архиве")],
)
def test_campaign_status_label_known(status: str, expected: str) -> None:
    assert campaign_status_label(status) == expected


def test_campaign_status_label_unknown_falls_back() -> None:
    assert campaign_status_label("weird") == "weird"


def _campaign(campaign_id: str, name: str, status: CampaignStatus) -> NeurocommentCampaign:
    return NeurocommentCampaign(
        campaign_id=campaign_id,
        name=name,
        prompt="p",
        status=status,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


def test_campaign_options_maps_id_to_status_labelled_name() -> None:
    campaigns = CampaignList(
        campaigns=[_campaign("c1", "Alpha", "active"), _campaign("c2", "Beta", "paused")],
    )
    assert campaign_options(campaigns) == {"c1": "Alpha · Активна", "c2": "Beta · На паузе"}


def test_campaign_options_empty_is_empty_dict() -> None:
    assert campaign_options(CampaignList()) == {}


def test_page_registration_is_importable() -> None:
    # Smoke test: registering the page builds the route without raising (mirrors
    # how the warming feature is smoke-covered).
    register_neurocomment_page()
