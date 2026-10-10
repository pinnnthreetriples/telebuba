"""The text that goes out: variants, variables, and the AI rewrite or first message.

The AI is an embellishment, never a gate: no key, the daily budget spent, a provider
error or an answer that fails the checks all send the operator's own text and log why.
The checks guard what the model may NOT do — drop or invent a link or a mention, use a
word the warming filter forbids, balloon the text — rather than ask it nicely.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from core.config import settings
from core.db import load_warming_settings
from core.logging import log_event
from services import content
from services._text_llm import text_llm
from services.chat_broadcast import _seams, _state
from services.chat_broadcast.links import render

if TYPE_CHECKING:
    from schemas.chat_broadcast import ChatBroadcastMessage
    from schemas.chat_broadcast_records import TargetRecord
    from services.chat_broadcast._context import RunContext

# Links and @mentions must survive a rewrite verbatim: they ARE the offer.
_KEEP_RE: Final = re.compile(r"(?:https?://\S+|t\.me/\S+|@[A-Za-z0-9_]{3,32})", re.IGNORECASE)
_QUOTES: Final = "\"'«»“”"
# Short texts may legitimately grow a little more than long ones.
_MIN_CEILING: Final = 200

_REWRITE_PROMPT: Final = (
    "Rewrite this Telegram group chat message in different words. Keep its meaning, "
    "tone and language, and keep every link, @mention and emoji exactly as written. "
    "Add nothing of your own and no new links. Answer with the new message text alone, "
    "no quotes around it.\n\nMessage:\n{text}"
)
_FIRST_PROMPT: Final = (
    "Write the first message for the Telegram group chat named {title!r}. "
    "Brief: {brief}\n"
    "Sound like an ordinary member of that chat: short, natural, on the chat's topic, "
    "in the chat's language. No links that the brief does not contain. Answer with "
    "the message text alone, no quotes around it."
)


def plain_text(message: ChatBroadcastMessage, target: TargetRecord) -> str:
    """The operator's text with one variant picked and the chat variables filled."""
    return render(message.text, title=target.title, username=target.username, rng=_seams.rng)


async def compose(
    ctx: RunContext, step_index: int, message: ChatBroadcastMessage, target: TargetRecord
) -> tuple[str, bool]:
    """``(text, written_by_ai)`` for one step of one chat."""
    base = plain_text(message, target)
    limit = 1024 if message.photo is not None else 4096
    if ctx.ai_first(step_index) and ctx.settings.ai_brief.strip():
        prompt = _FIRST_PROMPT.format(
            title=target.title or target.raw, brief=ctx.settings.ai_brief.strip()
        )
        written = await _ask(ctx, prompt, keep=ctx.settings.ai_brief, limit=limit)
        return (written, True) if written else (base, False)
    rewrite = ctx.settings.first_message == "template" and ctx.settings.randomize and base.strip()
    if rewrite:
        written = await _ask(ctx, _REWRITE_PROMPT.format(text=base), keep=base, limit=limit)
        if written:
            return written, True
    return base, False


async def _ask(ctx: RunContext, prompt: str, *, keep: str, limit: int) -> str | None:
    llm = text_llm(await load_warming_settings())
    if not llm.api_key:
        return await _fallback(ctx, "no_key")
    if _state.at_daily_llm_cap():
        return await _fallback(ctx, "daily_limit")
    _state.record_llm_call(calls=llm.max_retries + 1)
    generate = _seams.generate_text_deepseek if llm.use_deepseek else _seams.generate_text
    result = await generate(
        llm.request(prompt, max_output_tokens=settings.chat_broadcast.llm_max_output_tokens)
    )
    if result.status != "ok" or not result.text:
        return await _fallback(ctx, "provider_error")
    written = result.text.strip().strip(_QUOTES).strip()
    if not _acceptable(written, keep=keep, limit=limit):
        return await _fallback(ctx, "rejected")
    return written


def _acceptable(written: str, *, keep: str, limit: int) -> bool:
    if not written or len(written) > limit:
        return False
    ceiling = max(_MIN_CEILING, int(len(keep) * settings.chat_broadcast.rewrite_max_growth))
    if len(written) > ceiling:
        return False
    if content.has_forbidden_word(written, settings.warming.content_forbidden_words):
        return False
    wanted = {token.lower() for token in _KEEP_RE.findall(keep)}
    found = {token.lower() for token in _KEEP_RE.findall(written)}
    # Every link/mention the operator wrote, and none they did not.
    return found == wanted if wanted else not content.has_link(written)


async def _fallback(ctx: RunContext, reason: str) -> None:
    """Logged once per run and reason: a missing key would otherwise log every send."""
    if not _state.first_fallback(ctx.run_id, reason):
        return
    await log_event(
        "WARNING",
        "chat_broadcast_rewrite_fallback",
        extra={"campaign_id": ctx.campaign_id, "reason": reason},
    )
