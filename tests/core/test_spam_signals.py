"""The passive-signal port (``core.spam_signals``) and the mapping every gateway shares (T11).

The port is the only way ``core`` reports what an account's normal work showed: a no-op until the
app registers a receiver, and a fire-and-forget task that can neither slow nor fail the caller.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from core.config import settings
from core.spam_signals import drain_signals, emit_signal, set_signal_sink
from core.telegram_client._signals import (
    signal_freeze_config,
    signal_frozen,
    signal_me,
    signal_peer_flood,
    signal_read_peer_flood,
    signal_success,
)
from tests.core.conftest import Recorder


@pytest.mark.asyncio
async def test_with_no_receiver_an_emission_does_nothing() -> None:
    """The default: ``core`` alone (a script, a test) reports into nothing, and nothing breaks."""
    emit_signal("a1", "peer_flood_send", source="execute")

    await drain_signals()


@pytest.mark.asyncio
async def test_an_emission_reaches_the_receiver_once_with_what_was_reported(
    recorder: Recorder,
) -> None:
    emit_signal("a1", "peer_flood_send", source="execute", operation="send_dm", detail="X")
    assert recorder.seen == []  # scheduled, not awaited: the caller never waits on the sink

    await drain_signals()

    assert recorder.seen == [
        ("a1", "peer_flood_send", {"source": "execute", "op": "send_dm", "detail": "X"})
    ]


@pytest.mark.asyncio
async def test_a_failing_receiver_is_swallowed_and_logged_by_class_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(*_args: object, **_kwargs: object) -> None:
        msg = "secret detail the log must not carry"
        raise RuntimeError(msg)

    set_signal_sink(broken)
    with caplog.at_level(logging.WARNING, logger="core.spam_signals"):
        emit_signal("a1", "freeze_seen", source="execute")
        await drain_signals()

    assert "RuntimeError" in caplog.text
    assert "secret detail" not in caplog.text


@pytest.mark.asyncio
async def test_a_receiver_that_cannot_even_be_called_is_swallowed() -> None:
    """Not a coroutine function at all: the failure still stays inside the task."""

    def not_awaitable(*_args: object, **_kwargs: object) -> None:
        return None

    set_signal_sink(not_awaitable)  # ty: ignore[invalid-argument-type]
    emit_signal("a1", "freeze_seen", source="execute")

    await drain_signals()


@pytest.mark.asyncio
async def test_a_slow_receiver_does_not_hold_the_caller() -> None:
    release = asyncio.Event()
    finished: list[str] = []

    async def slow(*_args: object, **_kwargs: object) -> None:
        await release.wait()
        finished.append("done")

    set_signal_sink(slow)
    emit_signal("a1", "freeze_seen", source="execute")
    await asyncio.sleep(0)
    assert finished == []  # still waiting, and emit_signal has long returned

    release.set()
    await drain_signals()
    assert finished == ["done"]


def test_outside_an_event_loop_an_emission_is_dropped_quietly(recorder: Recorder) -> None:
    emit_signal("a1", "freeze_seen", source="execute")

    assert recorder.seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", ["enabled", "passive_signals"])
async def test_the_switches_turn_the_port_off(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    monkeypatch.setattr(settings.spam_intel, switch, False)

    emit_signal("a1", "peer_flood_send", source="execute")
    await drain_signals()

    assert recorder.seen == []


@pytest.mark.asyncio
async def test_registering_again_replaces_the_receiver_and_none_removes_it() -> None:
    first, second = Recorder(), Recorder()
    set_signal_sink(first)
    set_signal_sink(first)
    set_signal_sink(second)
    emit_signal("a1", "freeze_seen", source="execute")
    await drain_signals()
    set_signal_sink(None)
    emit_signal("a1", "freeze_seen", source="execute")
    await drain_signals()

    assert (first.kinds(), second.kinds()) == ([], ["freeze_seen"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "kind"),
    [
        ("send_dm", "peer_flood_send"),
        ("send_chat_message", "peer_flood_send"),
        ("post_comment", "peer_flood_send"),
        ("comment_on_post", "peer_flood_send"),
        ("copy_message_media", "peer_flood_send"),
        ("broadcast_send_message", "peer_flood_send"),
        ("broadcast_forward_post", "peer_flood_send"),
        ("join_channel", "peer_flood_other"),
        ("read_channel", "peer_flood_other"),
        ("update_profile", "peer_flood_other"),
        ("outreach_set_typing", "peer_flood_other"),
    ],
)
async def test_a_peer_flood_is_a_send_verdict_only_on_an_action_that_sends(
    recorder: Recorder, action_type: str, kind: str
) -> None:
    signal_peer_flood("a1", action_type)
    await drain_signals()

    assert recorder.seen == [
        ("a1", kind, {"source": "execute", "op": action_type, "detail": "PeerFlood"})
    ]


@pytest.mark.asyncio
async def test_a_peer_flood_on_a_read_is_the_weak_kind(recorder: Recorder) -> None:
    signal_read_peer_flood("a1")
    await drain_signals()

    assert recorder.seen == [
        ("a1", "peer_flood_other", {"source": "execute_read", "op": None, "detail": "PeerFlood"})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_type", "kind"),
    [
        ("post_comment", "group_send_ok"),
        ("comment_on_post", "group_send_ok"),
        ("broadcast_send_message", "group_send_ok"),
        ("broadcast_forward_post", "group_send_ok"),
        ("join_channel", "join_ok"),
        ("join_discussion_group", "join_ok"),
        ("broadcast_join_chatlist", "join_ok"),
        ("read_channel", "read_ok"),
    ],
)
async def test_a_success_is_a_shadow_kind_where_the_target_is_known(
    recorder: Recorder, action_type: str, kind: str
) -> None:
    signal_success("a1", action_type)
    await drain_signals()

    assert recorder.kinds() == [kind]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_type", ["send_chat_message", "send_dm", "update_profile", "set_online", "react_to_post"]
)
async def test_a_success_that_proves_nothing_about_the_target_is_not_reported(
    recorder: Recorder, action_type: str
) -> None:
    """A chat send may go to a user and a direct message to a known peer: not group sends."""
    signal_success("a1", action_type)
    await drain_signals()

    assert recorder.seen == []


@pytest.mark.asyncio
async def test_a_frozen_answer_is_a_freeze_with_its_class_name(recorder: Recorder) -> None:
    signal_frozen("a1", source="execute", operation="update_profile", error="FrozenMethodError")
    await drain_signals()

    assert recorder.seen == [
        (
            "a1",
            "freeze_seen",
            {"source": "execute", "op": "update_profile", "detail": "FrozenMethodError"},
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "kind", "detail"),
    [
        ({"freeze_since_date": 1_700_000_000}, "freeze_seen", None),
        (
            {"freeze_since_date": 1_700_000_000, "freeze_until_date": 1_800_000_000},
            "freeze_seen",
            "until=1800000000",
        ),
        ({"freeze_since_date": 0}, "freeze_cleared", None),
        ({}, "freeze_cleared", None),
    ],
)
async def test_the_app_config_the_check_fetched_is_a_freeze_or_its_closer(
    recorder: Recorder, fields: dict[str, object], kind: str, detail: str | None
) -> None:
    signal_freeze_config("a1", fields)
    await drain_signals()

    assert recorder.seen == [
        ("a1", kind, {"source": "session_check", "op": None, "detail": detail})
    ]


@pytest.mark.asyncio
async def test_a_restricted_user_is_a_platform_restriction_with_the_codes_not_the_text(
    recorder: Recorder,
) -> None:
    me = SimpleNamespace(
        restricted=True,
        restriction_reason=[
            SimpleNamespace(platform="all", reason="terms", text="Anna Ivanova, +7 999 000 00 00"),
            SimpleNamespace(platform="ios", reason="porn", text="x"),
        ],
    )
    signal_me("a1", me)
    await drain_signals()

    assert recorder.seen == [
        (
            "a1",
            "tos_restricted",
            {"source": "session_check", "op": None, "detail": "all:terms,ios:porn"},
        )
    ]


@pytest.mark.asyncio
async def test_the_restriction_reason_is_bounded_and_has_a_fallback(recorder: Recorder) -> None:
    many = [SimpleNamespace(platform="all", reason="r" * 40, text="") for _ in range(20)]
    signal_me("a1", SimpleNamespace(restricted=True, restriction_reason=many))
    signal_me("a2", SimpleNamespace(restricted=True))
    await drain_signals()

    details = {account: extra["detail"] for account, _kind, extra in recorder.seen}
    assert len(str(details["a1"])) == 200
    assert details["a2"] == "restricted"


@pytest.mark.asyncio
async def test_a_user_seen_unrestricted_is_the_closer_and_an_unset_flag_says_nothing(
    recorder: Recorder,
) -> None:
    signal_me("a1", SimpleNamespace(restricted=False))
    signal_me("a2", SimpleNamespace(id=7))  # no flag: a test double, or an unset TL field
    signal_me("a3", SimpleNamespace(restricted=None))
    await drain_signals()

    assert recorder.seen == [
        ("a1", "tos_cleared", {"source": "session_check", "op": None, "detail": None})
    ]
