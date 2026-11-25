"""A new chat-broadcast campaign starts with a pace its own hourly limit does not cut short."""

from __future__ import annotations

from schemas.chat_broadcast import ChatBroadcastSettings


def test_the_default_pause_between_chats_spreads_the_hourly_limit_over_the_hour() -> None:
    defaults = ChatBroadcastSettings()
    average = (defaults.between_chats.min + defaults.between_chats.max) / 2

    # Faster and the account sends its hour's messages in a burst, then sleeps; the settings
    # dialog warns about exactly that, so a fresh campaign must not open on the warning.
    assert average * defaults.per_hour == 3600
