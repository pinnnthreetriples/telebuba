"""Which LLM writes a warming chat line: DeepSeek when its key is set, else Gemini.

Warming dialogue is always plain text — it never carries the image that keeps
neurocomment's photo posts on Gemini — so this whole path can move. Its own module
rather than another case in ``test_chat.py``, which is already the big one, and
because the question here is not "did a DM go out" but "who wrote it".

The fallback half matters more than it looks: no DeepSeek key (neither the settings
page's nor ``.env``'s) is the only way an existing deployment stays on Gemini, and
warming runs unattended for days.
"""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import save_warming_settings
from schemas.gemini import GeminiRequest, GeminiResult
from schemas.warming import WarmingCycleRequest
from services import warming
from services.warming import _seams
from tests.services.warming._support import (
    _Recorder,
    _seed_channel,
    _seed_two_warming_accounts,
    _set_settings,
)


class _CapturingGen:
    """Records the requests so a test can name the provider that received them."""

    def __init__(self) -> None:
        self.requests: list[GeminiRequest] = []

    async def generate_text(self, request: GeminiRequest) -> GeminiResult:
        self.requests.append(request)
        return GeminiResult(status="ok", text="hi there")


class _ExplodingGen:
    """The provider that must not be reached."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def generate_text(self, _request: GeminiRequest) -> GeminiResult:
        msg = f"{self.name} wrote the chat line, but the other provider owns this case"
        raise AssertionError(msg)


async def _run_cycle(
    monkeypatch: pytest.MonkeyPatch, *, gemini_key: str = "gemini-key"
) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(_seams, "execute", recorder.execute)
    monkeypatch.setattr(settings.warming, "dm_min_age_hours", 0.0)
    await _seed_channel()
    await _set_settings(chat=True, reactions=False, key=gemini_key)
    await _seed_two_warming_accounts()
    await warming.run_one_cycle(WarmingCycleRequest(account_id="acc-1"))
    return recorder


@pytest.mark.asyncio
async def test_deepseek_writes_the_chat_line_when_its_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.deepseek, "api_key", "ds-key")
    monkeypatch.setattr(settings.deepseek, "model", "deepseek-v4-flash")
    deepseek = _CapturingGen()
    monkeypatch.setattr(_seams, "generate_text_deepseek", deepseek.generate_text)
    monkeypatch.setattr(_seams, "generate_text", _ExplodingGen("Gemini").generate_text)

    await _run_cycle(monkeypatch)

    assert deepseek.requests
    # The operator's Gemini key is seeded above, so asserting the key proves the
    # request was rebuilt for the new provider rather than merely re-routed.
    assert deepseek.requests[0].api_key == "ds-key"
    assert deepseek.requests[0].model == "deepseek-v4-flash"


@pytest.mark.asyncio
async def test_gemini_still_writes_it_when_deepseek_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.deepseek, "api_key", "")
    gemini = _CapturingGen()
    monkeypatch.setattr(_seams, "generate_text", gemini.generate_text)
    monkeypatch.setattr(_seams, "generate_text_deepseek", _ExplodingGen("DeepSeek").generate_text)

    await _run_cycle(monkeypatch)

    assert gemini.requests
    assert gemini.requests[0].api_key == "gemini-key"


@pytest.mark.asyncio
async def test_a_deepseek_key_alone_is_enough_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """The chat gate used to demand a Gemini key even though DeepSeek writes the line.

    The key is the settings page's stored one, not ``.env``'s — the path an operator
    who only ever pasted a DeepSeek key actually takes.
    """
    monkeypatch.setattr(settings.gemini, "api_key", "")  # no ambient .env fallback either
    await save_warming_settings(gemini_api_key=None, deepseek_api_key="ds-stored")
    deepseek = _CapturingGen()
    monkeypatch.setattr(_seams, "generate_text_deepseek", deepseek.generate_text)
    monkeypatch.setattr(_seams, "generate_text", _ExplodingGen("Gemini").generate_text)

    await _run_cycle(monkeypatch, gemini_key="")

    assert deepseek.requests
    assert deepseek.requests[0].api_key == "ds-stored"
