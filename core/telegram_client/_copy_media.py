"""Media copy: re-send someone else's photo or document as our own message.

A forward would render "Forwarded from …" with a link back to the source, which
defeats the point of a staged conversation, so the media is COPIED instead:
``send_file(chat, message.media, ...)`` hands Telethon the existing file reference
and nothing is uploaded again. Verified against Telethon 1.44.0 — ``caption`` and
``reply_to`` are both accepted on that path, and it covers photos as well as every
document kind (video, audio, gif, plain file).

Three traps live here, and each one is a silent wrong answer rather than an error:

* ``get_messages(chat, ids=<int>)`` answers ``None`` when this account cannot see
  the message. Reading ``.media`` off that is an ``AttributeError`` reported as an
  internal fault, so the ``None`` is checked first and refused by code.
* The media class must be checked, not merely its presence. ``MessageMediaEmpty``
  and ``MessageMediaUnsupported`` are accepted by ``send_file`` and produce a
  message with NO media; ``MessageMediaWebPage`` raises ``TypeError``.
* File references expire. ``FilerefUpgradeNeededError`` subclasses ``AuthKeyError``,
  which this gateway lists in ``_SESSION_ERRORS`` — untouched, a stale reference
  would tell the operator to re-login a perfectly healthy account. Telethon also
  maps only the exact string ``FILE_REFERENCE_EXPIRED``; the numbered variant
  ``FILE_REFERENCE_<n>_EXPIRED`` arrives as a bare ``BadRequestError``. Both shapes
  are caught, the source is re-read ONCE, and a second failure is raised as
  :class:`CopyMediaError` — a ``ValueError``, so it can never be mistaken for a dead
  session again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import errors

from core.telegram_client._action_results import _DispatchResult
from core.telegram_client._read_chat import media_kind, peer_reference
from schemas.telegram_actions_chat import COPYABLE_MEDIA_KINDS

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions_chat import CopyMessageMedia

# Telethon's mapped file-reference family. ``FilerefUpgradeNeededError`` is in the
# tuple precisely because its base class would otherwise route it to ``session_dead``.
_FILE_REFERENCE_ERRORS = (
    errors.FileReferenceExpiredError,
    errors.FileReferenceInvalidError,
    errors.FileReferenceEmptyError,
    errors.FilerefUpgradeNeededError,
)
# The unmapped numbered variant, recognised by its wire message.
_FILE_REFERENCE_TEXT = "FILE_REFERENCE"

# The three refusals this dispatcher can produce, as stable codes.
_SOURCE_MISSING = "media_source_missing"
_NOT_COPYABLE = "media_not_copyable"
_REFERENCE_STALE = "media_reference_stale"


class CopyMediaError(ValueError):
    """A media copy refused for a reason the operator can act on.

    Carries a stable ``code`` like the gateway's other refusal wrappers
    (``ProfileGatewayError``, ``ChannelGatewayError``), which is what
    ``_generic_error`` logs in place of the class name. Deliberately a ``ValueError``
    and NOT anything under ``AuthKeyError``: a stale file reference is not a dead
    session, and reporting it as one sends the operator to re-login a healthy account.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


async def _fetch_media(client: TelegramClient, action: CopyMessageMedia) -> tuple[object, str]:
    """The source message's media plus its kind, or a coded refusal.

    Re-read on every attempt on purpose: the reference is what goes stale, so a
    retry that reused the one already in hand would fail identically. Nothing about
    the source is ever cached in the database for the same reason.
    """
    message = await client.get_messages(
        peer_reference(action.source_chat),
        ids=action.source_message_id,
    )
    if message is None:
        raise CopyMediaError(_SOURCE_MISSING)
    media = getattr(message, "media", None)
    kind = media_kind(media)
    if kind not in COPYABLE_MEDIA_KINDS:
        raise CopyMediaError(_NOT_COPYABLE)
    return media, kind


async def dispatch_copy_message_media(
    client: TelegramClient,
    action: CopyMessageMedia,
) -> _DispatchResult:
    """Send the source message's media into ``chat_id`` as our own message.

    One retry, and one only: a reference that is still stale after a fresh read is
    not going to settle, and a longer loop would spend an account's send budget on a
    message that never lands.
    """
    stale: BaseException | None = None
    for _attempt in range(2):
        media, kind = await _fetch_media(client, action)
        try:
            sent = await client.send_file(
                action.chat_id,
                media,  # ty: ignore[invalid-argument-type]
                caption=action.caption or None,  # ty: ignore[invalid-argument-type]
                reply_to=action.reply_to,  # ty: ignore[invalid-argument-type]
            )
        # Ordered: the mapped family first, since every one of its members is itself an
        # ``RPCError`` subclass — three through ``BadRequestError`` and
        # ``FilerefUpgradeNeededError`` through ``AuthKeyError`` — so the text probe
        # below would otherwise shadow all four.
        except _FILE_REFERENCE_ERRORS as exc:
            stale = exc
        except errors.RPCError as exc:
            if _FILE_REFERENCE_TEXT not in str(exc):
                raise
            stale = exc
        else:
            return _DispatchResult(
                message_id=int(getattr(sent, "id", 0)) or None,
                log_extra={"media_kind": kind},
            )
    raise CopyMediaError(_REFERENCE_STALE) from stale
