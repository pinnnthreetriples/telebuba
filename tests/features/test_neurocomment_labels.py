"""Tests for the neurocomment page's pure-display label maps (issue #119).

The page itself is ``pragma: no cover`` (UI-thin, exercised manually); these two
translation helpers carry the only branchy logic, so they get a unit test.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from features.neurocomment import register_neurocomment_page
from features.neurocomment._logpanel import nc_event_label, nc_log_detail
from features.neurocomment._page import (
    PIPELINE_STEPS,
    board_captcha_count,
    board_content_signature,
    board_error_count,
    campaign_options,
    campaign_status_label,
    challenge_summary,
    channel_status_colors,
    channel_status_icon,
    channel_status_label,
    counter_window_since,
    fleet_activity,
    health_label,
    live_signature,
    relative_time,
    runtime_status_text,
    solver_switch_key,
    start_block_reason,
)
from schemas.challenge import ChallengeRow
from schemas.neurocomment import (
    CampaignList,
    CampaignStatus,
    ChannelStatus,
    NeurocommentAccountCard,
    NeurocommentBoard,
    NeurocommentCampaign,
    NeurocommentChannelRow,
    NeurocommentRuntimeStatus,
)


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


def test_channel_status_colors_ready_is_green() -> None:
    bg, color = channel_status_colors("ready")
    assert (bg, color) == ("#DDF7E9", "#12A150")


def test_channel_status_colors_unknown_falls_back_to_neutral() -> None:
    assert channel_status_colors("weird") == ("#ECEAE6", "#6B6864")


def test_channel_status_colors_known_statuses_all_mapped() -> None:
    # Every label-mapped status must also carry an explicit pill colour (no greys
    # leaking through for a status we have a Russian word for).
    for status in (
        "ready",
        "comments_off",
        "join_by_request",
        "chat_restricted",
        "bot_challenge",
        "bot_challenge_backoff",
        "throttled",
    ):
        assert channel_status_colors(status) != ("#ECEAE6", "#6B6864")


def test_start_block_reason_requires_a_listener() -> None:
    assert start_block_reason(5, has_listener=False) == "Выберите аккаунт-слушатель"


def test_start_block_reason_requires_a_ready_account() -> None:
    reason = start_block_reason(0, has_listener=True)
    assert reason is not None
    assert "готовых аккаунтов" in reason


def test_start_block_reason_none_when_ready() -> None:
    assert start_block_reason(3, has_listener=True) is None


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


def test_pipeline_steps_are_six_with_full_fields() -> None:
    # The rail + «Как работает» card both render these; they must stay aligned
    # with the six-step engine pipeline and carry no blank captions.
    assert len(PIPELINE_STEPS) == 6
    assert len({s.name for s in PIPELINE_STEPS}) == 6
    for step in PIPELINE_STEPS:
        assert step.label
        assert step.icon
        assert step.detail


def _card(account_id: str, *, health: str, last_hour: int, today: int) -> NeurocommentAccountCard:
    return NeurocommentAccountCard(
        account_id=account_id,
        label=account_id,
        health=health,
        trust_score=50,
        trust_band="good",
        comments_last_hour=last_hour,
        max_comments_per_hour=10,
        comments_today=today,
    )


def _channel(channel: str, status: ChannelStatus) -> NeurocommentChannelRow:
    return NeurocommentChannelRow(
        channel=channel,
        status=status,
        ready_accounts=0,
        total_accounts=0,
    )


def test_fleet_activity_sums_accounts_and_channels() -> None:
    board = NeurocommentBoard(
        campaign_id="c1",
        campaign_name="A",
        status="active",
        accounts=[
            _card("a1", health="ready", last_hour=2, today=5),
            _card("a2", health="blocked", last_hour=1, today=3),
        ],
        channels=[_channel("@x", "ready"), _channel("@y", "throttled")],
    )

    activity = fleet_activity(board)

    assert activity.comments_last_hour == 3
    assert activity.comments_today == 8
    assert activity.ready_accounts == 1
    assert activity.total_accounts == 2
    assert activity.ready_channels == 1
    assert activity.total_channels == 2


def test_fleet_activity_empty_board_is_all_zero() -> None:
    activity = fleet_activity(
        NeurocommentBoard(campaign_id="c1", campaign_name="A", status="active"),
    )
    assert activity == (0, 0, 0, 0, 0, 0)


def test_board_captcha_count_sums_challenge_channels() -> None:
    board = NeurocommentBoard(
        campaign_id="c1",
        campaign_name="A",
        status="active",
        channels=[
            _channel("@a", "ready"),
            _channel("@b", "bot_challenge"),
            _channel("@c", "bot_challenge_backoff"),
            _channel("@d", "throttled"),
        ],
    )
    assert board_captcha_count(board) == 2


def test_board_captcha_count_none_is_zero() -> None:
    assert board_captcha_count(None) == 0


def test_board_error_count_counts_non_ready_accounts() -> None:
    board = NeurocommentBoard(
        campaign_id="c1",
        campaign_name="A",
        status="active",
        accounts=[
            _card("a1", health="ready", last_hour=0, today=0),
            _card("a2", health="blocked", last_hour=0, today=0),
            _card("a3", health="blocked", last_hour=0, today=0),
        ],
    )
    assert board_error_count(board) == 2


def test_board_error_count_none_is_zero() -> None:
    assert board_error_count(None) == 0


def test_pipeline_step_labels_match_design_spec() -> None:
    # The rail nodes follow the design's NSTEPS ordering/wording (spec C.4).
    assert [s.label for s in PIPELINE_STEPS] == [
        "Новый пост",
        "Выбор аккаунта",
        "Генерация",
        "Публикация",
        "Проверка",
        "Готово",
    ]


@pytest.mark.parametrize("iso", [None, "", "not-a-date"])
def test_relative_time_missing_or_invalid_is_none(iso: str | None) -> None:
    assert relative_time(iso, datetime(2026, 6, 24, 12, tzinfo=UTC)) is None


def test_relative_time_buckets() -> None:
    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
    assert relative_time("2026-06-24T11:59:40+00:00", now) == "только что"
    assert relative_time("2026-06-24T11:30:00+00:00", now) == "30 мин назад"
    assert relative_time("2026-06-24T09:00:00+00:00", now) == "3 ч назад"
    assert relative_time("2026-06-22T12:00:00+00:00", now) == "2 д назад"


def test_relative_time_naive_timestamp_assumed_utc() -> None:
    now = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
    assert relative_time("2026-06-24T11:30:00", now) == "30 мин назад"


def test_runtime_status_text_stopped() -> None:
    assert runtime_status_text(NeurocommentRuntimeStatus(running=False)) == "Движок остановлен"


def test_runtime_status_text_running_with_channels() -> None:
    status = NeurocommentRuntimeStatus(running=True, active_channels=3)
    assert runtime_status_text(status) == "Слушаю каналов: 3"


def test_runtime_status_text_running_no_channels() -> None:
    status = NeurocommentRuntimeStatus(running=True, active_channels=0)
    assert runtime_status_text(status) == "Движок запущен"


def _board(*, today: int = 5) -> NeurocommentBoard:
    return NeurocommentBoard(
        campaign_id="c1",
        campaign_name="A",
        status="active",
        accounts=[_card("a1", health="ready", last_hour=1, today=today)],
        channels=[_channel("@x", "ready")],
    )


def test_board_content_signature_stable_for_identical_boards() -> None:
    assert board_content_signature(_board()) == board_content_signature(_board())


def test_board_content_signature_changes_when_a_card_field_changes() -> None:
    assert board_content_signature(_board(today=5)) != board_content_signature(_board(today=6))


def test_board_content_signature_none_is_empty() -> None:
    assert board_content_signature(None) == ()


def test_live_signature_changes_on_running_flip() -> None:
    activity = fleet_activity(_board())
    stopped = NeurocommentRuntimeStatus(running=False)
    running = NeurocommentRuntimeStatus(running=True, active_channels=2)
    assert live_signature(stopped, activity, None) != live_signature(running, activity, None)


def test_live_signature_changes_on_last_comment_bucket() -> None:
    status = NeurocommentRuntimeStatus(running=True, active_channels=1)
    activity = fleet_activity(_board())
    assert live_signature(status, activity, "5 мин назад") != live_signature(
        status, activity, "6 мин назад"
    )


def test_live_signature_stable_when_nothing_changed() -> None:
    status = NeurocommentRuntimeStatus(running=True, active_channels=1)
    activity = fleet_activity(_board())
    assert live_signature(status, activity, "только что") == live_signature(
        status, activity, "только что"
    )


def test_nc_event_label_known_event() -> None:
    icon, label = nc_event_label("neurocomment_posted")
    assert icon == "send"
    assert label == "Комментарий отправлен"


def test_nc_event_label_unknown_humanizes_without_prefix() -> None:
    assert nc_event_label("neurocomment_some_new_event") == ("circle", "some new event")


def test_nc_log_detail_post_skipped_uses_reason_and_channel() -> None:
    detail = nc_log_detail("neurocomment_post_skipped", {"channel": "@x", "reason": "forward"})
    assert detail == "@x: forward"


def test_nc_log_detail_cooldown_seconds() -> None:
    extra = {"channel": "@x", "cooldown_seconds": 3600}
    assert nc_log_detail("neurocomment_channel_backoff", extra) == "@x · пауза 3600 с"


def test_nc_log_detail_missing_count() -> None:
    detail = nc_log_detail("neurocomment_channel_backoff", {"channel": "@x", "missing": 4})
    assert detail == "@x · удалено 4"


def test_nc_log_detail_channel_backoff_shows_both_missing_and_cooldown() -> None:
    # The real deletion-back-off event carries both — neither must shadow the other.
    extra = {"channel": "@x", "missing": 5, "cooldown_seconds": 3600}
    assert nc_log_detail("neurocomment_channel_backoff", extra) == "@x · удалено 5 · пауза 3600 с"


def test_nc_log_detail_falls_back_to_joined_fields() -> None:
    detail = nc_log_detail("neurocomment_posted", {"channel": "@x"})
    assert detail == "@x"


def test_nc_log_detail_empty_extra_is_blank() -> None:
    assert nc_log_detail("neurocomment_runtime_reconciled", {}) == ""


def test_page_registration_is_importable() -> None:
    # Smoke test: registering the page builds the route without raising (mirrors
    # how the warming feature is smoke-covered).
    register_neurocomment_page()
