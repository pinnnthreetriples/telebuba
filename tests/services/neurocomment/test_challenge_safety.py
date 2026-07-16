"""Tests for neurocomment challenge safety behavior."""

from __future__ import annotations

import pytest

from core.config import settings
from core.db import (
    insert_challenge,
    resolve_pending_outcome,
)
from schemas.challenge import ChallengeDecision, ChallengeInsert
from schemas.gemini import GeminiResult
from schemas.telegram_actions import (
    ClickButton,
)
from services.neurocomment import _seams, challenge
from tests.services.neurocomment.challenge_support import (
    _challenge_rows,
    _decision_text,
    _ExecuteStub,
    _gemini,
    _msg,
    _wait,
)

pytestmark = pytest.mark.usefixtures("isolate_challenge")

# --- H3: image decisions must never pollute the text cache ---


@pytest.mark.asyncio
async def test_image_challenge_row_hash_is_photo_namespaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A photo challenge's audit row is keyed under the photo-namespaced hash, distinct
    # from the plain text hash, so a same-text/labels TEXT challenge can never reuse it.
    message = _msg(has_photo=True, image_b64="aW1n")
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(_seams, "execute_read", _wait(message))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=True).execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"

    photo_hash = challenge._challenge_hash(message.text, message.button_labels, has_photo=True)
    plain_hash = challenge._challenge_hash(message.text, message.button_labels)
    assert [r["challenge_hash"] for r in _challenge_rows()] == [photo_hash]
    assert photo_hash != plain_hash


@pytest.mark.asyncio
async def test_image_outcome_not_reused_by_text_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Solve an image challenge and mark it solved (cache-eligible), then present a TEXT
    # challenge with the same text+labels: the photo namespace misses → fresh Gemini call.
    photo = _msg(has_photo=True, image_b64="aW1n")
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    monkeypatch.setattr(_seams, "execute_read", _wait(photo))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", _ExecuteStub(ok=True).execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)
    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert await resolve_pending_outcome("acc-1", "@chan", "solved") is True

    text_msg = _msg()  # same text + labels, no photo
    monkeypatch.setattr(_seams, "execute_read", _wait(text_msg))
    assert await challenge.solve_if_present("acc-2", "@chan2", 42) == "solved"
    assert len(gemini.calls) == 2  # cache MISS → the text challenge asked Gemini afresh


# --- M4: gate on confidence (fresh decisions only) ---


@pytest.mark.asyncio
async def test_low_confidence_gives_up_without_acting(monkeypatch: pytest.MonkeyPatch) -> None:
    # A fresh decision below the confidence floor is dropped (→ give_up), nothing dispatched.
    gemini = _gemini(
        GeminiResult(
            status="ok",
            text=_decision_text(action="click_button", button_index=0, confidence=0.5),
        ),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []  # never acted on a low-confidence guess
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_confidence_at_threshold_acts(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exactly at the floor (0.7) is allowed to act (the gate is a strict <).
    monkeypatch.setattr(settings.neurocomment, "challenge_min_confidence", 0.7)
    gemini = _gemini(
        GeminiResult(
            status="ok",
            text=_decision_text(action="click_button", button_index=0, confidence=0.7),
        ),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert isinstance(execute.calls[0], ClickButton)


@pytest.mark.asyncio
async def test_cached_low_confidence_decision_still_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The confidence floor gates only FRESH LLM calls; a cached (already-vetted) solved
    # decision is reused even if its stored confidence is below the floor.
    message = _msg()
    decision = ChallengeDecision(
        action="click_button", button_index=0, confidence=0.3, reasoning="r"
    )
    await insert_challenge(
        ChallengeInsert(
            challenge_hash=challenge._challenge_hash(message.text, message.button_labels),
            account_id="other-acc",
            channel="@other",
            raw_text=message.text,
            button_labels=message.button_labels,
            outcome="solved",
            decision_json=decision.model_dump_json(),
        ),
    )
    gemini = _gemini(GeminiResult(status="error"))
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(message))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    assert gemini.calls == []  # cached decision reused, never re-vetted for confidence
    assert isinstance(execute.calls[0], ClickButton)


# --- C2: screen a send_text action with the comment outbound filter ---


@pytest.mark.asyncio
async def test_send_text_with_link_gives_up_and_does_not_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A send_text answer containing a link fails the outbound filter → give_up, no post.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="join t.me/foo")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []  # nothing posted
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_send_text_with_forbidden_word_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # A forbidden marketing word ("купить") in the answer trips the outbound filter.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="купить сейчас")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []


@pytest.mark.asyncio
async def test_send_text_over_word_cap_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # An answer longer than the comment word cap is refused (same guard as the comment path).
    monkeypatch.setattr(settings.neurocomment, "comment_max_words", 2)
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="send_text", text="one two three")),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg()))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []


# --- C1: screen the button label the dispatch will click ---


@pytest.mark.asyncio
async def test_click_dangerous_button_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # Gemini picks a denylisted button ("Connect Wallet") → the solver refuses to click it.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(
        _seams, "execute_read", _wait(_msg(button_labels=["Connect Wallet", "Skip"]))
    )
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []
    assert [r["outcome"] for r in _challenge_rows()] == ["give_up"]


@pytest.mark.asyncio
async def test_click_url_button_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    # A button whose label is a link is treated as dangerous even without a denylist hit.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(
        _seams, "execute_read", _wait(_msg(button_labels=["t.me/joinme", "cancel"]))
    )
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "give_up"
    assert execute.calls == []


@pytest.mark.asyncio
async def test_click_benign_button_still_clicks(monkeypatch: pytest.MonkeyPatch) -> None:
    # A safe label passes the screen and is clicked normally.
    gemini = _gemini(
        GeminiResult(status="ok", text=_decision_text(action="click_button", button_index=0)),
    )
    execute = _ExecuteStub(ok=True)
    monkeypatch.setattr(_seams, "execute_read", _wait(_msg(button_labels=["I am human", "cancel"])))
    monkeypatch.setattr(_seams, "generate_text", gemini)
    monkeypatch.setattr(_seams, "execute", execute.execute)
    monkeypatch.setattr(_seams.rng, "lognormvariate", lambda _mu, _sigma: 0.0)

    assert await challenge.solve_if_present("acc-1", "@chan", 99) == "solved"
    click = execute.calls[0]
    assert isinstance(click, ClickButton)
    assert click.button_text == "I am human"
