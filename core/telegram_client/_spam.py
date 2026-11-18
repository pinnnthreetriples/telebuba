"""@SpamBot probe + self-restriction read for one account.

Two ways to ask. :func:`check_spam_status` is the single ``/start`` probe that has always been
here. :func:`converse_spam_status` is the human-like conversation (``SPAM_INTEL__HUMAN_SEQUENCE``,
docs/spam-intel.md 5.7): open the dialog, pause, "typing", pause, ``/start``, collect every
message of the bot until it has been quiet for a while, pause, mark the dialog read.

What the conversation NEVER does, by construction and pinned by ``tests/test_architecture.py``:
press a button, answer the bot, delete the dialog, send anything but ``/start``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from telethon import events
from telethon.tl import functions, types

from core.config import settings
from core.logging import log_event
from core.telegram_client._pool import get_client
from core.telegram_client._util import optional_str
from schemas.spam_probe import HumanProbePlan, SpambotTranscript
from schemas.spam_status import SpamBotHistory, SpamStatusProbe

if TYPE_CHECKING:
    from collections.abc import Iterable

    from telethon import TelegramClient, hints

# Stdlib sink for full third-party text — see ``core.proxy_check._failed_result``.
logger = logging.getLogger(__name__)

_SPAMBOT_USERNAME = "SpamBot"
# The one message the program ever sends to the bot.
_START_COMMAND = "/start"
# A bot that never goes quiet does not hold the conversation open for ever.
_MAX_TRANSCRIPT_MESSAGES = 20


async def check_spam_status(account_id: str) -> SpamStatusProbe:
    """Probe @SpamBot and read self-restriction flags for an account.

    Sends ``/start`` to @SpamBot and captures its reply, and reads the account's
    own ``restricted`` / ``restriction_reason`` flags via ``get_me`` (carried as data: they are
    a platform restriction, and no classifier reads them as a spam limit). The raw
    result is parsed and cached by ``services.spam_status`` — never raises.

    Runs on the pooled client for the same reason the session check does: a
    second Telethon client on an account whose ``.session`` file is already open
    dies on ``database is locked`` (see ``_session._probe_client``).
    """
    try:
        client = await get_client(account_id)
        reply_text = await _probe_spambot(client)
        restricted, reason = await _probe_self_restriction(client)
    except Exception as exc:  # noqa: BLE001 - any probe failure classifies as unknown.
        return await _probe_failed(account_id, exc)
    return SpamStatusProbe(
        account_id=account_id,
        reply_text=reply_text,
        restricted=restricted,
        restriction_reason=reason,
    )


async def converse_spam_status(account_id: str, plan: HumanProbePlan) -> SpamStatusProbe:
    """Ask @SpamBot the way a person would, on the account's pooled client — never raises.

    The transcript comes back as a probe: ``texts`` (every message with text), ``has_markup``
    and ``n_messages``, with ``reply_text`` the texts joined. ``restricted`` stays ``False``:
    the probe no longer calls ``get_me`` (the session check reads that flag where it already
    runs). A bot that says nothing within ``plan.reply_wait_max`` is a ``TimeoutError``, as it
    is for the single probe.
    """
    try:
        client = await get_client(account_id)
        transcript = await converse_with_spambot(client, plan)
    except Exception as exc:  # noqa: BLE001 - any probe failure classifies as unknown.
        return await _probe_failed(account_id, exc)
    return SpamStatusProbe(
        account_id=account_id,
        reply_text="\n\n".join(transcript.texts) or None,
        texts=transcript.texts,
        has_markup=transcript.has_markup,
        n_messages=transcript.n_messages,
    )


async def _probe_failed(account_id: str, exc: Exception) -> SpamStatusProbe:
    """Record a failed probe and turn it into the verdict-less result callers classify."""
    # The class name only, no traceback: the exception's text can carry a proxy endpoint or a
    # session path, and ``logger.exception`` prints it again.
    logger.warning("spam status probe failed for %s: %s", account_id, type(exc).__name__)
    await log_event(
        "WARNING",
        "telegram_spam_status_probe_failed",
        account_id=account_id,
        extra={"error_type": type(exc).__name__},
    )
    # Class name only, never ``str(exc)``. ``services.spam_status`` copies this
    # into ``SpamStatusVerdict.detail``, which is the response model of
    # ``POST /accounts/{id}/spam-check`` and is re-served as
    # ``AccountRead.spam_detail`` — rendered verbatim in the operator's
    # browser. A pooled-client failure stringifies with the proxy endpoint and
    # a session fault with the ``.session`` path (non-negotiable #12, the same
    # call ``_read``, ``_auth`` and ``services.accounts.privacy`` make).
    #
    # NOT because "the full text is safely in the WARNING above": ``log_event``
    # is not log-only. It inserts a ``logs`` row with ``extra`` as JSON, and
    # ``GET /logs`` serves that back as ``LogEntry.extra`` while ``GET /events``
    # streams it — so the WARNING is an HTTP body too, by a different route
    # (verified: the probe's ``message`` came back verbatim in
    # ``GET /api/v1/logs``). Bounding this field removed the DUPLICATE; the
    # ``extra`` above is bounded too and neither it nor the stdlib logger carries
    # the text (the local debug log is not a place for a proxy endpoint either).
    # What justifies the class name here is the response contract on its own — a
    # locale-neutral code the SPA can translate.
    return SpamStatusProbe(account_id=account_id, error=type(exc).__name__)


async def _probe_spambot(client: TelegramClient) -> str | None:
    """Send ``/start`` to @SpamBot and return the next message it sends back.

    Uses ``events.NewMessage`` rather than ``client.conversation`` to close a
    documented race in Telethon: the conversation context registers its update
    handler inside ``__aenter__``, so a fast-replying bot can deliver its reply
    in the window between ``connect()`` and the handler being live. That reply
    then lands in the general update pool with no one waiting for it, and the
    conversation times out even though the bot did answer. The Telethon docs
    state the rule explicitly: "you should get a 'handle' of this special
    coroutine before acting."

    Here we register the handler BEFORE ``send_message``, filter by sender so
    we don't grab unrelated traffic, and coordinate via ``asyncio.Future``
    with our own ``wait_for`` timeout — the exact pattern the docs recommend
    for waiting on a single message.
    """
    bot = await client.get_input_entity(_SPAMBOT_USERNAME)
    loop = asyncio.get_running_loop()
    reply_future: asyncio.Future[str | None] = loop.create_future()

    async def on_reply(event: events.NewMessage.Event) -> None:
        if not reply_future.done():
            reply_future.set_result(optional_str(getattr(event, "raw_text", None)))

    client.add_event_handler(on_reply, events.NewMessage(from_users=bot, incoming=True))
    try:
        await client.send_message(bot, _START_COMMAND)
        return await asyncio.wait_for(
            reply_future,
            timeout=settings.telegram.timeout_seconds,
        )
    finally:
        client.remove_event_handler(on_reply)


async def _probe_self_restriction(client: TelegramClient) -> tuple[bool, str | None]:
    """Read the account's own ``restricted`` flag + reason (terms/country)."""
    me = await client.get_me()
    restricted = bool(getattr(me, "restricted", False))
    reasons = getattr(me, "restriction_reason", None) or []
    reason = "; ".join(
        str(getattr(item, "text", "") or getattr(item, "reason", "")) for item in reasons
    ).strip("; ")
    return restricted, (reason or None)


async def _pause(seconds: float) -> None:
    """The human sequence's sleep seam: tests replace it so a conversation runs instantly."""
    if seconds > 0:
        await asyncio.sleep(seconds)


@dataclass
class _Inbox:
    """What @SpamBot has sent so far; the handler fills it, the collector watches it."""

    texts: list[str] = field(default_factory=list)
    count: int = 0
    has_markup: bool = False
    arrived: asyncio.Event = field(default_factory=asyncio.Event)

    async def on_reply(self, event: events.NewMessage.Event) -> None:
        if self.count >= _MAX_TRANSCRIPT_MESSAGES:
            return  # the cap lives here: a burst delivered before the collector looks is cut too
        self.count += 1
        if text := optional_str(getattr(event, "raw_text", None)):
            self.texts.append(text)
        # Seen, never touched: a keyboard or inline buttons on the message.
        self.has_markup = self.has_markup or getattr(event, "reply_markup", None) is not None
        self.arrived.set()


async def _collect(inbox: _Inbox, plan: HumanProbePlan) -> None:
    """Wait for the bot's first message, then until it has been quiet for the plan's span.

    Raises ``TimeoutError`` when nothing at all arrives within ``plan.reply_wait_max``.
    """
    if inbox.count == 0:
        await asyncio.wait_for(inbox.arrived.wait(), timeout=plan.reply_wait_max)
    while inbox.count < _MAX_TRANSCRIPT_MESSAGES:
        inbox.arrived.clear()
        try:
            await asyncio.wait_for(inbox.arrived.wait(), timeout=plan.quiet_after_last_reply)
        except TimeoutError:
            return


async def _mark_read(client: TelegramClient, bot: hints.EntityLike) -> None:
    """``messages.readHistory`` on the dialog; the reading is done, so a failure costs nothing."""
    try:
        await client.send_read_acknowledge(bot)
    except Exception as exc:  # noqa: BLE001 - cosmetic step, the transcript is already in hand.
        logger.warning("spambot read acknowledge failed: %s", type(exc).__name__)


async def converse_with_spambot(
    client: TelegramClient,
    plan: HumanProbePlan,
) -> SpambotTranscript:
    """Talk to @SpamBot as a person would and return everything it said.

    Resolve the bot (cached after the first time), pause, open the dialog
    (``messages.getHistory``), pause, show "typing", pause, send ``/start`` and collect every
    message of the bot until ``plan.quiet_after_last_reply`` seconds pass with none, pause, mark
    the dialog read. The handler is registered BEFORE the send (the race the single probe
    closed: a fast bot can answer before a late handler exists).

    Never presses a button, never answers, never deletes the dialog, sends nothing but
    ``/start``, and never calls ``get_me``.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    bot = await client.get_input_entity(_SPAMBOT_USERNAME)
    await _pause(plan.pre_open)
    await client.get_messages(bot, limit=1)
    await _pause(plan.open_to_typing)
    await client(
        functions.messages.SetTypingRequest(peer=bot, action=types.SendMessageTypingAction())
    )
    await _pause(plan.typing_seconds)
    inbox = _Inbox()
    handler = inbox.on_reply
    client.add_event_handler(handler, events.NewMessage(from_users=bot, incoming=True))
    try:
        await client.send_message(bot, _START_COMMAND)
        await _collect(inbox, plan)
    finally:
        client.remove_event_handler(handler)
    await _pause(plan.read_delay)
    await _mark_read(client, bot)
    return SpambotTranscript(
        texts=inbox.texts,
        has_markup=inbox.has_markup,
        n_messages=inbox.count,
        elapsed=loop.time() - started,
    )


async def read_spambot_history(account_id: str, limit: int) -> SpamBotHistory:
    """Read what @SpamBot already said to this account, without sending anything.

    One ``messages.getHistory`` on the existing dialog (plus the username resolution, which
    the session caches): the bot's earlier replies are still there, because nothing ever
    deleted the dialog. Nothing is marked read and no ``/start`` goes out. Never raises: a
    failure is the exception class name in ``error``.

    Runs on the pooled client, like the probe (a second client on an open ``.session`` file
    dies on ``database is locked``).
    """
    try:
        client = await get_client(account_id)
        bot = await client.get_input_entity(_SPAMBOT_USERNAME)
        messages = await client.get_messages(bot, limit=limit)
    except Exception as exc:  # noqa: BLE001 - any read failure is a class name, nothing more.
        logger.warning("spambot history read failed for %s: %s", account_id, type(exc).__name__)
        await log_event(
            "WARNING",
            "telegram_spam_history_failed",
            account_id=account_id,
            extra={"error_type": type(exc).__name__},
        )
        return SpamBotHistory(account_id=account_id, error=type(exc).__name__)
    texts = [
        text
        for message in cast("Iterable[object]", messages)
        if not getattr(message, "out", False)
        and (text := optional_str(getattr(message, "raw_text", None)))
    ]
    return SpamBotHistory(account_id=account_id, texts=texts)
