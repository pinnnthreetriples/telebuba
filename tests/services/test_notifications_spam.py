"""Spam notifications (design 5.12, D16): a limit or a freeze the operator hears about once.

The ``spam_limited`` kind names the account, the day the limit ends or "бессрочно", and the source
from an enumerated map, never a reply's words. A verdict that was already limited, an ending
limit and a chat-ban refusal send nothing. A freeze shares one slot with the session-death
alert, and a PeerFlood limit on an account whose warming loop runs is left to ``quarantine``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

from core.config import settings
from core.db import create_account, upsert_warming_state
from core.logging import reset_logging_for_tests, setup_logging
from core.repositories.notification_settings import save_notification_settings
from schemas.accounts import AccountCreate, AccountRead
from schemas.logs import LogEntry
from schemas.notifications import BotMessage, BotSendResult
from schemas.warming import WarmingStateWrite
from services import notifications
from services._notification_messages import compose_message, notification_kind
from services._notification_spam import SpamNotice

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_TOKEN = "123456:AAH-secret_token_value_abcdefXYZ"
_VERDICT = "spam_intel_verdict_changed"

# The fixed appeal hint the ``spam_limited`` message ends with.
_APPEAL_HINT = "Апелляцию подаёте вы сами: на карточке аккаунта кнопка «Подать апелляцию»."


class _Bot:
    def __init__(self) -> None:
        self.sent: list[BotMessage] = []

    async def __call__(self, message: BotMessage) -> BotSendResult:
        self.sent.append(message)
        return BotSendResult(status="sent")


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    notifications.reset_for_tests()
    yield
    notifications.reset_for_tests()
    reset_logging_for_tests()


@pytest.fixture
def bot(monkeypatch: pytest.MonkeyPatch) -> _Bot:
    stub = _Bot()
    monkeypatch.setattr(notifications, "send_bot_message", stub)

    async def _fetch(account_id: str) -> AccountRead:
        return AccountRead(
            account_id=account_id,
            status="alive",
            phone="+79991234567",
            first_name="Анна",
            username="anna_w",
            created_at="now",
            updated_at="now",
        )

    monkeypatch.setattr(notifications, "fetch_account", _fetch)
    return stub


def _entry(event: str, account_id: str | None = "acc-1", **extra: object) -> LogEntry:
    return LogEntry(
        id=1,
        created_at="now",
        level="INFO",
        status="success",
        account_id=account_id,
        event=event,
        extra=dict(extra),
    )


def _changed(
    to: str,
    frm: str | None = "unknown",
    kind: str | None = "spambot_limited",
    account_id: str = "acc-1",
    **more: object,
) -> LogEntry:
    entry = _entry(_VERDICT, account_id)
    entry.extra = {"from": frm, "to": to, "primary_kind": kind, "until": None, **more}
    return entry


async def _configure() -> None:
    await save_notification_settings(enabled=True, bot_token=_TOKEN, chat_id="-100500")


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        (_changed("limited"), "spam_limited"),
        (_changed("limited", frm="clean", kind="peer_flood_send"), "spam_limited"),
        (_changed("limited", frm=None, kind="peer_flood_other"), "spam_limited"),
        (_changed("limited", frm="frozen"), "spam_limited"),
        (_changed("frozen", kind="freeze_seen"), "account_frozen"),
        (_changed("frozen", frm="clean", kind="freeze_seen"), "account_frozen"),
        # Not news: already limited, already frozen, a verdict that only ends or turns clean.
        (_changed("limited", frm="limited", kind="peer_flood_send"), None),
        (_changed("frozen", frm="frozen", kind="freeze_seen"), None),
        (_changed("unknown", frm="limited", kind=None), None),
        (_changed("clean", frm="limited", kind="spambot_clean"), None),
        (_changed("clean", frm="unknown", kind="spambot_clean"), None),
        # A chat-ban refusal moves the fold but its scope is not the account's: no ping.
        (_changed("limited", kind="user_banned_in_channel"), None),
        (_changed("limited", kind=None), None),
        # The session-death alert for a freeze is the frozen alert; for a dead key it stays.
        (_entry("account_session_dead", status="frozen"), "account_frozen"),
        (_entry("account_session_dead", status="unauthorized"), "session_dead"),
        # The @SpamBot reading path, unchanged: news only.
        (_entry("spam_status_refreshed", status="limited", previous="clean"), "spam_limited"),
        (_entry("spam_status_refreshed", status="limited", previous="limited"), None),
        (_entry("spam_status_refreshed", status="clean", previous="limited"), None),
    ],
)
def test_which_rows_are_news(entry: LogEntry, kind: str | None) -> None:
    assert notification_kind(entry) == kind


def test_a_dated_limit_names_the_day_and_the_source() -> None:
    notice = SpamNotice(until=date(2026, 10, 29), source="@SpamBot")
    text = compose_message("spam_limited", "Анна (@anna_w)", notice)
    expected = "Telebuba · Анна (@anna_w): аккаунт ограничен за спам — до 29.10.2026."
    assert text == f"{expected} Источник: @SpamBot. {_APPEAL_HINT}"


def test_the_limit_message_points_to_the_appeal_button_and_says_the_operator_files_it() -> None:
    """Design 5.12: the program opens the window; a human appeals. A fixed text, no reply quoted."""
    for notice in (
        SpamNotice(until=date(2026, 10, 29), source="@SpamBot"),
        SpamNotice(until=None, source="PeerFlood"),
        None,
    ):
        text = compose_message("spam_limited", "Анна", notice)
        assert text.endswith(_APPEAL_HINT)
    assert "Подать апелляцию" in _APPEAL_HINT
    assert "сами" in _APPEAL_HINT


def test_an_undated_limit_says_open_ended() -> None:
    text = compose_message("spam_limited", "Анна", SpamNotice(until=None, source="PeerFlood"))
    assert "бессрочно" in text
    assert "Источник: PeerFlood." in text


def test_a_message_without_a_notice_still_names_the_account_and_no_date() -> None:
    text = compose_message("spam_limited", "Анна")
    assert text.startswith("Telebuba · Анна: аккаунт ограничен за спам")
    assert "бессрочно" in text
    assert "Источник" not in text


def test_the_frozen_message_names_the_account_and_what_it_means() -> None:
    text = compose_message("account_frozen", "Анна (@anna_w)")
    assert "Анна (@anna_w)" in text
    assert "заморозил" in text
    assert "passkey" not in text  # a freeze is not a dead key: no "log in again" advice


@pytest.mark.asyncio
async def test_a_passive_limit_sends_the_day_and_the_source_from_the_enum(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(
        _changed("limited", frm="clean", kind="peer_flood_send", until="2026-10-29")
    )
    assert [m.text for m in bot.sent] == [
        "Telebuba · Анна (@anna_w): аккаунт ограничен за спам — до 29.10.2026. "
        f"Источник: PeerFlood при отправке. {_APPEAL_HINT}"
    ]


@pytest.mark.asyncio
async def test_a_hostile_until_value_is_no_date_and_extra_text_never_reaches_the_message(
    bot: _Bot,
) -> None:
    await _configure()
    await notifications.handle_log_entry(
        _changed("limited", kind="spambot_limited", until="Send me your password", reply="SECRET")
    )
    (message,) = bot.sent
    assert "бессрочно" in message.text
    assert "password" not in message.text
    assert "SECRET" not in message.text


@pytest.mark.asyncio
async def test_the_spambot_reading_path_names_the_date_it_parsed_from_the_reply(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(
        _entry(
            "spam_status_refreshed",
            status="limited",
            previous="clean",
            detail="Your account is limited until 29 Oct 2026, 10:00 UTC. Contact the admin.",
        )
    )
    (message,) = bot.sent
    assert message.text.endswith(f"до 29.10.2026. Источник: @SpamBot. {_APPEAL_HINT}")
    assert "Contact the admin" not in message.text


@pytest.mark.asyncio
async def test_an_undated_spambot_limit_is_open_ended(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(
        _entry("spam_status_refreshed", status="limited", previous=None, detail=None)
    )
    assert "бессрочно" in bot.sent[0].text


@pytest.mark.asyncio
async def test_the_reading_and_the_verdict_it_causes_send_one_message(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(
        _entry("spam_status_refreshed", status="limited", previous="clean", detail=None)
    )
    await notifications.handle_log_entry(_changed("limited", frm="clean"))
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_the_throttle_is_per_account_and_kind(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(_changed("limited", kind="spambot_limited"))
    await notifications.handle_log_entry(_changed("limited", kind="spambot_limited"))
    await notifications.handle_log_entry(_changed("limited", account_id="acc-2"))
    await notifications.handle_log_entry(_changed("frozen", kind="freeze_seen"))
    assert len(bot.sent) == 3


@pytest.mark.asyncio
async def test_a_freeze_is_told_once_whichever_alert_comes_first(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(_entry("account_session_dead", status="frozen"))
    await notifications.handle_log_entry(_changed("frozen", kind="freeze_seen"))
    assert len(bot.sent) == 1
    assert "заморозил" in bot.sent[0].text


@pytest.mark.asyncio
async def test_a_silent_expiry_sends_nothing(bot: _Bot) -> None:
    await _configure()
    await notifications.handle_log_entry(
        _changed("unknown", frm="limited", kind=None, reason="limit_expired")
    )
    assert bot.sent == []


async def _warming(account: str, state: str) -> None:
    await create_account(AccountCreate(account_id=account))
    await upsert_warming_state(
        WarmingStateWrite(
            account_id=account,
            state=state,  # ty: ignore[invalid-argument-type]
            started_at="2026-11-10T00:00:00+00:00",
            warmed_days=2,
            run_id="run-1",
            next_run_at="2026-11-17T00:00:00+00:00",
            wave=1,
        )
    )


@pytest.mark.asyncio
async def test_a_peer_flood_limit_on_an_account_in_quarantine_is_left_to_quarantine(
    bot: _Bot,
) -> None:
    await _warming("acc-1", "quarantine")
    await _configure()
    await notifications.handle_log_entry(_changed("limited", frm="clean", kind="peer_flood_send"))
    assert bot.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "sleeping", "flood_wait"])
async def test_a_running_loop_alone_does_not_hide_a_peer_flood_limit(bot: _Bot, state: str) -> None:
    """Audit F-023: only the matching quarantine alert is a duplicate, not the loop's state."""
    await _warming("acc-1", state)
    await _configure()
    await notifications.handle_log_entry(_changed("limited", frm="clean", kind="peer_flood_send"))
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_a_peer_flood_limit_after_its_quarantine_alert_is_not_told_twice(
    bot: _Bot,
) -> None:
    await _warming("acc-1", "active")  # the state has not caught up with the alert yet
    await _configure()
    await notifications.handle_log_entry(_entry("warming_cycle_completed", status="peer_flood"))
    assert len(bot.sent) == 1  # the quarantine alert itself

    await notifications.handle_log_entry(_changed("limited", frm="clean", kind="peer_flood_send"))

    assert len(bot.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["idle", "error", "spam_blocked"])
async def test_a_peer_flood_limit_on_a_parked_warming_card_is_still_told(
    bot: _Bot, state: str
) -> None:
    await _warming("acc-1", state)
    await _configure()
    await notifications.handle_log_entry(_changed("limited", frm="clean", kind="peer_flood_send"))
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_an_spambot_limit_is_told_even_while_warming_runs(bot: _Bot) -> None:
    await _warming("acc-1", "active")
    await _configure()
    await notifications.handle_log_entry(_changed("limited", frm="clean", kind="spambot_limited"))
    assert len(bot.sent) == 1


# --- a new limit after an ended one is news (audit F-023) ---------------------------------------


def _reading(previous: str | None, previous_detail: str | None, when: str) -> LogEntry:
    entry = _entry(
        "spam_status_refreshed",
        status="limited",
        detail="until 20 Dec 2026",
        previous=previous,
        previous_detail=previous_detail,
    )
    entry.created_at = when
    return entry


@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        # The previous limit ran out before this reading: a NEW limit, told.
        (_reading("limited", "until 29 Oct 2026", "2026-11-17T12:00:00+00:00"), "spam_limited"),
        # Still on at this reading, or undated (it never ends by itself): a repeat, not told.
        (_reading("limited", "until 29 Nov 2026", "2026-11-17T12:00:00+00:00"), None),
        (_reading("limited", None, "2026-11-17T12:00:00+00:00"), None),
        (_reading("clean", None, "2026-11-17T12:00:00+00:00"), "spam_limited"),
    ],
)
def test_a_limit_after_an_ended_one_is_news_a_repeat_is_not(
    entry: LogEntry, kind: str | None
) -> None:
    assert notification_kind(entry) == kind
