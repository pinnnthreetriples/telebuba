"""T13 — the human-like @SpamBot probe against a fake client (``converse_with_spambot``).

The fake records every call in order, so the sequence (resolve, open the dialog, typing, send,
collect until quiet, read) is asserted as a list, not inferred. Nothing here reaches Telegram.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from core.config import settings
from core.db import configure_database
from core.logging import reset_logging_for_tests, setup_logging
from core.telegram_client import _spam, converse_spam_status
from core.telegram_client._spam import converse_with_spambot
from schemas.spam_probe import HumanProbePlan, SpambotTranscript

if TYPE_CHECKING:
    from telethon import TelegramClient

PLAN = HumanProbePlan(
    pre_open=1.5,
    open_to_typing=2.5,
    typing_seconds=1.25,
    reply_wait_max=0.5,
    quiet_after_last_reply=0.05,
    read_delay=3.5,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    configure_database(tmp_path / "telebuba.db")
    monkeypatch.setattr(settings.telegram, "session_dir", tmp_path / "sessions")
    monkeypatch.setattr(settings.logging, "path", tmp_path / "debug.log")
    monkeypatch.setattr(settings.logging, "sentry_dsn", "")
    reset_logging_for_tests()
    setup_logging()
    yield
    reset_logging_for_tests()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    """The shared call log; the sleep seam writes its pauses into it too."""
    log: list[tuple[str, Any]] = []

    async def _record_pause(seconds: float) -> None:
        log.append(("pause", seconds))

    monkeypatch.setattr(_spam, "_pause", _record_pause)
    return log


class _FakeClient:
    """Records every call in order and answers ``/start`` with a scripted list of messages."""

    def __init__(self, log: list[tuple[str, Any]], replies: list[SimpleNamespace]) -> None:
        self.log = log
        self.replies = replies
        self.handler: Any = None
        self.send_log: list[str] = []

    async def get_input_entity(self, username: str) -> str:
        self.log.append(("resolve", username))
        return username

    async def get_messages(self, peer: object, *, limit: int) -> list[object]:
        self.log.append(("history", (peer, limit)))
        return []

    async def __call__(self, request: object) -> None:
        self.log.append(("request", type(request).__name__))

    def add_event_handler(self, handler: object, _event: object) -> None:
        self.log.append(("add_handler", None))
        self.handler = handler

    def remove_event_handler(self, handler: object) -> None:
        self.log.append(("remove_handler", None))
        if self.handler is handler:
            self.handler = None

    async def send_message(self, _peer: object, text: str) -> None:
        self.log.append(("send", text))
        self.send_log.append(text)
        assert self.handler is not None, "the handler must exist before the send"
        for reply in self.replies:
            await self.handler(reply)

    async def send_read_acknowledge(self, peer: object) -> None:
        self.log.append(("read", peer))


async def _converse(client: _FakeClient) -> SpambotTranscript:
    """The conversation against the fake (the fake stands in for a Telethon client)."""
    return await converse_with_spambot(cast("TelegramClient", client), PLAN)


def _reply(text: str | None, *, markup: object = None) -> SimpleNamespace:
    return SimpleNamespace(raw_text=text, reply_markup=markup)


@pytest.mark.asyncio
async def test_the_sequence_is_resolve_open_typing_send_collect_read(
    calls: list[tuple[str, Any]],
) -> None:
    client = _FakeClient(calls, [_reply("Good news, no limits.")])

    transcript = await _converse(client)

    assert calls == [
        ("resolve", "SpamBot"),
        ("pause", PLAN.pre_open),
        ("history", ("SpamBot", 1)),
        ("pause", PLAN.open_to_typing),
        ("request", "SetTypingRequest"),
        ("pause", PLAN.typing_seconds),
        ("add_handler", None),
        ("send", "/start"),
        ("remove_handler", None),
        ("pause", PLAN.read_delay),
        ("read", "SpamBot"),
    ]
    assert transcript.texts == ["Good news, no limits."]
    assert transcript.n_messages == 1
    assert transcript.has_markup is False


@pytest.mark.asyncio
async def test_every_message_of_the_bot_is_collected_in_order(
    calls: list[tuple[str, Any]],
) -> None:
    client = _FakeClient(calls, [_reply("first"), _reply("second"), _reply("third")])

    transcript = await _converse(client)

    assert transcript.texts == ["first", "second", "third"]
    assert transcript.n_messages == 3


@pytest.mark.asyncio
async def test_a_message_without_text_counts_but_adds_no_text(
    calls: list[tuple[str, Any]],
) -> None:
    client = _FakeClient(calls, [_reply(None), _reply("words")])

    transcript = await _converse(client)

    assert (transcript.n_messages, transcript.texts) == (2, ["words"])


@pytest.mark.asyncio
async def test_markup_is_seen_and_never_touched(calls: list[tuple[str, Any]]) -> None:
    client = _FakeClient(calls, [_reply("Pick one", markup=object())])

    transcript = await _converse(client)

    assert transcript.has_markup is True
    assert {name for name, _ in calls} <= {
        "resolve",
        "pause",
        "history",
        "request",
        "add_handler",
        "send",
        "remove_handler",
        "read",
    }


@pytest.mark.asyncio
async def test_only_start_is_ever_sent_and_the_dialog_is_neither_deleted_nor_answered(
    calls: list[tuple[str, Any]],
) -> None:
    client = _FakeClient(calls, [_reply("Do you want to appeal?", markup=object())])

    await _converse(client)

    assert client.send_log == ["/start"]
    # The only request through ``client(...)`` is the typing action.
    assert [detail for name, detail in calls if name == "request"] == ["SetTypingRequest"]


@pytest.mark.asyncio
async def test_no_get_me_in_the_conversation(calls: list[tuple[str, Any]]) -> None:
    """A fake with no ``get_me`` at all: calling it would raise ``AttributeError``."""
    client = _FakeClient(calls, [_reply("hello")])
    assert not hasattr(client, "get_me")

    await _converse(client)


@pytest.mark.asyncio
async def test_silence_is_a_timeout_and_the_handler_is_still_removed(
    calls: list[tuple[str, Any]],
) -> None:
    client = _FakeClient(calls, [])

    with pytest.raises(TimeoutError):
        await _converse(client)

    assert ("remove_handler", None) in calls
    assert ("read", "SpamBot") not in calls  # nothing was read, so nothing is marked read


@pytest.mark.asyncio
async def test_a_failed_read_acknowledge_keeps_the_transcript(
    calls: list[tuple[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(calls, [_reply("Good news")])

    async def _boom(_peer: object) -> None:
        msg = "flood"
        raise RuntimeError(msg)

    monkeypatch.setattr(client, "send_read_acknowledge", _boom)

    transcript = await _converse(client)

    assert transcript.texts == ["Good news"]


@pytest.mark.asyncio
async def test_a_bot_that_never_goes_quiet_is_cut_at_the_message_cap(
    calls: list[tuple[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No real timer decides this: the quiet span is long, so only the cap can end it."""
    client = _FakeClient(calls, [])
    chatter: list[asyncio.Task[None]] = []
    patient = PLAN.model_copy(update={"quiet_after_last_reply": 30.0, "reply_wait_max": 30.0})

    async def _keeps_talking(_peer: object, text: str) -> None:
        client.send_log.append(text)

        async def _talk() -> None:
            for index in range(_spam._MAX_TRANSCRIPT_MESSAGES * 3):
                await asyncio.sleep(0)  # a yield, not a timer: the next message is always due
                if client.handler is None:
                    return
                await client.handler(_reply(f"m{index}"))

        chatter.append(asyncio.create_task(_talk()))

    monkeypatch.setattr(client, "send_message", _keeps_talking)

    transcript = await converse_with_spambot(cast("TelegramClient", client), patient)
    await asyncio.gather(*chatter)

    assert transcript.n_messages == _spam._MAX_TRANSCRIPT_MESSAGES
    assert len(transcript.texts) == _spam._MAX_TRANSCRIPT_MESSAGES


@pytest.mark.asyncio
async def test_a_burst_that_arrives_before_the_send_returns_is_cut_at_the_cap(
    calls: list[tuple[str, Any]],
) -> None:
    """The cap lives in the handler: 25 replies delivered at once are still 20 texts."""
    burst = [_reply(f"m{index}") for index in range(_spam._MAX_TRANSCRIPT_MESSAGES + 5)]
    client = _FakeClient(calls, burst)

    transcript = await _converse(client)

    assert transcript.n_messages == _spam._MAX_TRANSCRIPT_MESSAGES
    assert transcript.texts == [f"m{index}" for index in range(_spam._MAX_TRANSCRIPT_MESSAGES)]


def _patch_pool(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    async def fake_get_client(_account_id: str, _device_type: str | None = None) -> object:
        return client

    monkeypatch.setattr("core.telegram_client._spam.get_client", fake_get_client)


@pytest.mark.asyncio
async def test_the_account_entry_returns_the_transcript_as_a_probe(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, Any]],
) -> None:
    _patch_pool(monkeypatch, _FakeClient(calls, [_reply("one"), _reply("two", markup=object())]))

    probe = await converse_spam_status("acc-1", PLAN)

    assert probe.error is None
    assert probe.texts == ["one", "two"]
    assert probe.reply_text == "one\n\ntwo"
    assert (probe.n_messages, probe.has_markup, probe.restricted) == (2, True, False)


@pytest.mark.asyncio
async def test_the_account_entry_never_raises_and_names_the_class_only(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, Any]],
) -> None:
    async def failing_get_client(_account_id: str, _device_type: str | None = None) -> object:
        msg = "proxy 203.0.113.9:1080 refused"
        raise ConnectionError(msg)

    monkeypatch.setattr("core.telegram_client._spam.get_client", failing_get_client)

    probe = await converse_spam_status("acc-1", PLAN)

    assert probe.error == "ConnectionError"
    assert probe.reply_text is None
    assert not calls


@pytest.mark.asyncio
async def test_a_silent_bot_is_a_timeout_error_for_the_executor(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[tuple[str, Any]],
) -> None:
    _patch_pool(monkeypatch, _FakeClient(calls, []))

    probe = await converse_spam_status("acc-1", PLAN)

    assert probe.error == "TimeoutError"
