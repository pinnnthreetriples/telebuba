"""What a run reads once when it starts: the settings, the chain, the photos.

Settings cannot change under a run (a save is refused while one is attached), so one
read per run is the whole truth — except the pauses the board's gear edits, which the
run reads live through :attr:`RunContext.pace`. A photo that is missing on disk makes
its step a text-only send rather than failing the chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.chat_broadcast_media import BroadcastMediaError, read_photo
from core.repositories import chat_broadcast as repository
from schemas.chat_broadcast import ChatBroadcastPace
from services.chat_broadcast import _state
from services.chat_broadcast.campaigns import settings_of
from services.chat_broadcast.links import parse_post

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastMessage, ChatBroadcastSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunContext:
    campaign_id: str
    run_id: str
    settings: ChatBroadcastSettings
    # The steps that will actually be sent, in order: empty messages dropped, a repeated
    # message present once per copy — as the SAME object, which is how a copy of the
    # first message is still told apart as "the first message".
    chain: list[ChatBroadcastMessage]
    photos: dict[str, bytes] = field(default_factory=dict)
    started_unix: int = 0

    @property
    def rounds(self) -> int | None:
        """How many rounds the run plays; ``None`` = until stopped."""
        if not self.settings.loop:
            return 1
        return self.settings.rounds or None

    @property
    def pace(self) -> ChatBroadcastPace:
        """The pauses as they are now: the gear's latest save, else the run's own copy."""
        return _state.live_pace(self.campaign_id) or pace_of(self.settings)

    def ai_first(self, step_index: int) -> bool:
        """Is this step a copy of the first message, which the AI writes per chat?"""
        return (
            self.settings.first_message == "ai"
            and self.chain[0].kind == "text"
            and self.chain[step_index] is self.chain[0]
        )


def pace_of(value: ChatBroadcastSettings) -> ChatBroadcastPace:
    return ChatBroadcastPace(
        between_messages=value.between_messages,
        between_chats=value.between_chats,
        rest_minutes=value.rest_minutes,
    )


def usable_chain(value: ChatBroadcastSettings) -> list[ChatBroadcastMessage]:
    """The steps of one round: text, a photo or a valid post link, each ``repeat`` times."""
    chain: list[ChatBroadcastMessage] = []
    for index, message in enumerate(value.messages):
        if message.kind == "post":
            usable = parse_post(message.post) is not None
        else:
            ai_writes_it = index == 0 and value.first_message == "ai" and value.ai_brief.strip()
            usable = bool(message.text.strip() or message.photo is not None or ai_writes_it)
        if usable:
            chain.extend([message] * message.repeat)
    return chain


async def load_context(campaign_id: str, run_id: str) -> RunContext | None:
    # Before the read, not after: a gear save landing during it then survives as the
    # override instead of being dropped with a copy the read may have missed.
    _state.forget_pace(campaign_id)
    record = await repository.fetch_campaign(campaign_id)
    if record is None:
        return None
    parsed = settings_of(record)
    chain = usable_chain(parsed)
    photos: dict[str, bytes] = {}
    for message in chain:
        if message.photo is None or message.photo.media_id in photos:
            continue
        try:
            photos[message.photo.media_id] = await read_photo(message.photo.media_id)
        except BroadcastMediaError as exc:
            # The step still goes out, as text: a lost file must not stop the chain.
            logger.warning(
                "photo %s of campaign %s unusable: %s",
                message.photo.media_id,
                campaign_id,
                exc.code,
            )
    return RunContext(
        campaign_id=campaign_id,
        run_id=run_id,
        settings=parsed,
        chain=chain,
        photos=photos,
        started_unix=record.started_unix or 0,
    )
