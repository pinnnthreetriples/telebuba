"""What a run reads once when it starts: the settings, the chain, the photos.

Settings cannot change under a run (a save is refused while one is attached), so one
read per run is the whole truth. A photo that is missing on disk makes its step a
text-only send rather than failing the chain.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.chat_broadcast_media import BroadcastMediaError, read_photo
from core.repositories import chat_broadcast as repository
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
    # The messages that will actually be sent, in order; empty ones are dropped.
    chain: list[ChatBroadcastMessage]
    photos: dict[str, bytes] = field(default_factory=dict)
    started_unix: int = 0

    @property
    def rounds(self) -> int | None:
        """How many rounds the run plays; ``None`` = until stopped."""
        if not self.settings.loop:
            return 1
        return self.settings.rounds or None

    def ai_first(self, step_index: int) -> bool:
        return (
            step_index == 0 and self.settings.first_message == "ai" and self.chain[0].kind == "text"
        )


def usable_chain(value: ChatBroadcastSettings) -> list[ChatBroadcastMessage]:
    """The messages that can be sent: text, a photo, or a valid post link."""
    chain: list[ChatBroadcastMessage] = []
    for index, message in enumerate(value.messages):
        if message.kind == "post":
            if parse_post(message.post) is not None:
                chain.append(message)
            continue
        ai_writes_it = index == 0 and value.first_message == "ai" and value.ai_brief.strip()
        if message.text.strip() or message.photo is not None or ai_writes_it:
            chain.append(message)
    return chain


async def load_context(campaign_id: str, run_id: str) -> RunContext | None:
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
