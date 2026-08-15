"""Ask DeepSeek for a dialogue and turn what comes back into a saveable scenario.

DeepSeek only. ``services.neurocomment._llm`` falls back to Gemini when the
deployment has no DeepSeek key, and that is right for the comment hot path — but
the Gemini key is an operator-set secret on the warming settings row, and reaching
for it here would drag warming state into a request that has none.
``expand_discovery_keywords`` made the same call for the same reason: an unset
``DEEPSEEK__API_KEY`` is a deployment fact the operator can act on, so it is
reported rather than worked around.

Three properties this module is responsible for, none of which the provider gives:

* **Retry is mandatory, not defensive.** DeepSeek documents that JSON mode "may
  occasionally return empty content". A single attempt would surface that as a
  failed generation the operator can only re-click.
* **The complaint is fed back.** Each retry appends what the previous answer got
  wrong, so the model repairs its own output rather than being asked the identical
  question again. The wording is always OUR validator's — a provider error string
  never travels anywhere, here or into the operator's view.
* **The answer is normalised, not trusted.** Positions are renumbered from the
  steps that survive, a line spoken by nobody in the cast is dropped, and a
  ``reply_to_index`` that points forwards, at itself, or at a dropped step simply
  resolves to nothing. Repairing beats re-asking for all of these: they cost one
  line each, and burning a paid call on a line is worse than losing it.

Thinking is off. ``thinking_budget`` defaults to ``0``, which ``core.openai``
renders as ``{"type": "disabled"}`` — for cost and latency, not for correctness:
DeepSeek returns reasoning in ``reasoning_content`` ALONGSIDE ``content``, so the
answer still arrives, but the thoughts are billed to ``max_tokens`` and would eat
the budget the dialogue needs while adding seconds to a shape that needs none.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import ValidationError

from core.config import settings
from schemas.gemini import GeminiRequest
from schemas.neuroshilling_scenario import (
    NeuroshillingDialogueDraft,
    NeuroshillingRoleInput,
    NeuroshillingScenarioUpdate,
    NeuroshillingStepInput,
)
from services.neuroshilling import _seams, _state
from services.neuroshilling._prompt import build_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

    from schemas.gemini import GeminiResult
    from schemas.neuroshilling_scenario import NeuroshillingDraftStep

# Models wrap JSON in a fence about as often as they are told not to.
_CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

_EMPTY_COMPLAINT = "it was empty or unparseable; answer with json and nothing else"
_CUT_OFF_COMPLAINT = "it was cut off mid-answer; write fewer and shorter steps"
# Enough to point the model at the field, short enough not to crowd the prompt out.
_MAX_COMPLAINT_CHARS = 300
# The floor ``NeuroshillingGenerateRequest.step_count`` already declares: below two
# steps there is no dialogue left to shrink to.
_MIN_STEPS = 2


def _unwrap(text: str) -> str:
    return _CODE_FENCE.sub("", text.strip())


def _validation_complaint(exc: ValidationError) -> str:
    """Turn pydantic's own report into one line the model can act on.

    Built from ``errors()`` rather than ``str(exc)`` so the text is a list of field
    paths and pydantic's own messages — bounded, ours, and free of anything a
    provider or a third-party library put in an exception.
    """
    parts = [
        f"{'.'.join(str(piece) for piece in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    ]
    return "; ".join(parts)[:_MAX_COMPLAINT_CHARS]


def _draft_problem(draft: NeuroshillingDialogueDraft, persona_count: int) -> str | None:
    """What is wrong with a structurally valid answer, in the model's own terms.

    Only faults worth a fresh call are listed. Everything repairable — a link
    pointing the wrong way, a line with nothing in it — is handled by
    :func:`_to_update` instead, because a retry costs money and a repair does not.
    """
    if len(draft.roles) != persona_count:
        return f"roles must contain exactly {persona_count} entries"
    if not draft.steps:
        return "steps was empty"
    if all(step.reaction is not None for step in draft.steps):
        return "at least one step must be a reply rather than a reaction"
    return None


def _read(
    result: GeminiResult,
    persona_count: int,
) -> tuple[NeuroshillingDialogueDraft | None, str]:
    """``(draft, complaint)`` — exactly one of the two is meaningful."""
    if result.status != "ok" or result.text is None:
        # Errors, rate limits and a 200 carrying no text collapse into one
        # complaint on purpose. ``GeminiResult.error`` is built as
        # ``f"{type(exc).__name__}: {exc}"``, so it can carry third-party prose;
        # it is never read here and never reaches the operator.
        return None, _EMPTY_COMPLAINT
    try:
        draft = NeuroshillingDialogueDraft.model_validate_json(_unwrap(result.text))
    except ValidationError as exc:
        return None, _validation_complaint(exc)
    problem = _draft_problem(draft, persona_count)
    return (None, problem) if problem is not None else (draft, "")


def _step_input(
    step: NeuroshillingDraftStep,
    target: int | None,
    role_key: str,
) -> NeuroshillingStepInput:
    if step.reaction is not None:
        return NeuroshillingStepInput(
            kind="reaction",
            role_id=role_key,
            target_position=target,
            emoji=step.reaction,
        )
    return NeuroshillingStepInput(
        kind="message", role_id=role_key, text=step.text.strip(), reply_to_position=target
    )


def _to_update(
    draft: NeuroshillingDialogueDraft,
    role_ids: Sequence[str],
) -> NeuroshillingScenarioUpdate:
    """Renumber the surviving steps and rewire their links to the new positions.

    ``position_of`` is filled AFTER a step is kept, which is what makes a forward
    or self-reference resolve to nothing without a separate check: at the moment a
    step's link is looked up, only earlier surviving steps are in the map.

    ``role_ids`` are the campaign's STORED role ids, reused positionally. The model
    has no idea which roles already exist, so without this every generated role
    would carry a key matching nothing, the keyed upsert would mint new ids and
    delete the old ones, and ``neuroshilling_accounts.role_id`` — an
    ``ON DELETE SET NULL`` foreign key — would come back null for the whole roster.
    """
    keys = [
        role_ids[index] if index < len(role_ids) else str(index + 1)
        for index in range(len(draft.roles))
    ]
    roles = [
        NeuroshillingRoleInput(role_id=key, name=role.name, description=role.description)
        for key, role in zip(keys, draft.roles, strict=True)
    ]
    position_of: dict[int, int] = {}
    steps: list[NeuroshillingStepInput] = []
    for index, step in enumerate(draft.steps):
        if step.speaker_id > len(keys):
            continue  # a speaker outside the cast has no role to say the line
        if step.reaction is None and not step.text.strip():
            continue  # a reply with nothing in it would post an empty message
        target = None if step.reply_to_index is None else position_of.get(step.reply_to_index)
        if step.reaction is not None and target is None:
            continue  # a reaction with nothing to react to has no meaning
        position_of[index] = len(steps) + 1
        steps.append(_step_input(step, target, keys[step.speaker_id - 1]))
    return NeuroshillingScenarioUpdate(roles=roles, steps=steps)


def _request(prompt: str) -> GeminiRequest:
    return GeminiRequest(
        api_key=settings.deepseek.api_key,
        prompt=prompt,
        model=settings.deepseek.model,
        temperature=settings.deepseek.temperature,
        max_output_tokens=settings.neuroshilling.llm_max_output_tokens,
        # DeepSeek's only JSON mode; a ``json_schema`` request is refused outright.
        response_json_object=True,
    )


async def generate_dialogue(
    topic: str,
    *,
    persona_count: int,
    step_count: int,
    unique_messages: bool,
    role_ids: Sequence[str],
) -> NeuroshillingScenarioUpdate | None:
    """Ask for a dialogue, up to ``llm_max_attempts`` times. ``None`` = nothing usable.

    ``None`` covers an unset key, an exhausted retry budget and a budget that ran
    out mid-loop: the caller answers 503 for all of them, because in every case no
    dialogue exists and the operator's next move is the same.
    """
    if not settings.deepseek.api_key:
        return None
    complaint: str | None = None
    ask = step_count
    for _attempt in range(settings.neuroshilling.llm_max_attempts):
        if _state.at_daily_llm_cap():
            # Re-read every pass rather than trusted from the door: the claim in
            # ``_state.try_start_generation`` tests the cap without reserving
            # against it, so two campaigns clicked together at cap-1 both pass —
            # and a long retry chain can cross the line by itself.
            break
        prompt = build_prompt(
            topic,
            persona_count=persona_count,
            step_count=ask,
            unique_messages=unique_messages,
            complaint=complaint,
        )
        # One attempt is ``max_retries + 1`` HTTP requests: ``core.openai`` retries a
        # transient failure INSIDE the call, and charging one would undercount a
        # maxed-out configuration six-fold. Charged at the worst case, before the
        # call: the cap is a ceiling on spend, so erring high is the safe direction,
        # and a crash mid-call must not leave the spend uncounted.
        _state.record_llm_call(calls=settings.deepseek.max_retries + 1)
        result = await _seams.generate_text_deepseek(_request(prompt))
        if result.status == "truncated":
            # Re-asking the identical question under the identical token cap runs
            # out of tokens in exactly the same place. Shrinking the ask is the only
            # thing that changes the answer, so the retry is worth paying for.
            ask, complaint = max(_MIN_STEPS, ask // 2), _CUT_OFF_COMPLAINT
            continue
        draft, complaint = _read(result, persona_count)
        if draft is not None:
            return _to_update(draft, role_ids)
    return None
