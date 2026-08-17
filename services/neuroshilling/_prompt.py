"""What the scenario generator asks the model, and the shape it demands back.

Its own module from the first line, because a prompt plus its response contract
plus a retry loop in one file is how ``_generate`` would reach the 440-line gate
on its second edit.

Two things live here and nothing else: the fence that keeps the operator's text
from breaking the prompt's own structure, and the composer.

The contract is carried by the rules and by ONE worked example, not by a rendered
JSON Schema. DeepSeek's JSON mode accepts no schema, so a schema in the prompt
enforces nothing that ``NeuroshillingDialogueDraft`` does not already enforce on
the way back — it was a second copy of the same contract, in the format a model
follows worst. DeepSeek's own guidance is to include the word "json" and an
example of the desired output, and both are below.
"""

from __future__ import annotations

import json
import re
from typing import get_args

from schemas.neuroshilling_scenario import NeuroshillingReaction

# The tag THIS module writes, both directions, any case, whitespace anywhere
# inside the brackets. Copied in shape from ``services.neurocomment._llm``, NOT in
# content: that one is compiled for ``<post>``/``<comment>``, and reusing it
# verbatim would strip a marker this prompt never writes while leaving the one it
# does — a topic containing ``</topic>`` would then close the block early and
# everything after it would read as instruction.
_FENCE_TAG = re.compile(r"<\s*/?\s*topic\s*>", re.IGNORECASE)

# Bound on ONE generated line. The dialogue has to fit in
# ``GeminiRequest.max_output_tokens``' 2048-token ceiling: a Cyrillic sentence
# costs roughly one token per two characters, so twelve lines of this length plus
# the JSON scaffolding and the persona blurbs land near 1500 and leave headroom. A
# cut arrives as ``finish_reason: "length"``, which the gateway reports as
# ``truncated`` rather than a short success — the attempt is lost and ``_generate``
# halves the step count before asking again.
_MAX_LINE_CHARS = 200

_EXAMPLE = json.dumps(
    {
        "roles": [
            {"name": "Skeptic", "description": "Doubts everything, asks for proof."},
            {"name": "Regular", "description": "Has used it for a year, answers calmly."},
        ],
        "steps": [
            {
                "speaker_id": 1,
                "text": "Has anyone actually tried this?",
                "reply_to_index": None,
                "reaction": None,
            },
            {
                "speaker_id": 2,
                "text": "A year now, no complaints.",
                "reply_to_index": 0,
                "reaction": None,
            },
            {"speaker_id": 1, "text": "", "reply_to_index": 1, "reaction": "\U0001f44d"},
        ],
    },
    ensure_ascii=False,
)


def strip_fence_tags(text: str) -> str:
    """Remove every fence tag from operator text, repeating until nothing changes.

    The loop is the point. A single pass does not survive a SPLIT marker: fed
    ``</to</topic>pic>`` it deletes the inner tag and the halves left behind close
    up into a live ``</topic>``, so the strip has to reach a fixed point.
    Termination is free — any iteration that changes the string shortens it.

    Hygiene, not a privilege boundary: the topic is the OPERATOR's own text and
    they can already type any prompt they like. What it buys is that the prompt's
    structure survives a paste, and that stage six's genuinely untrusted chat text
    has a stripper to extend rather than a second one to write.
    """
    while True:
        stripped = _FENCE_TAG.sub("", text)
        if stripped == text:
            return stripped
        text = stripped


def _rules(persona_count: int, step_count: int, *, unique_messages: bool) -> str:
    variety = (
        "Give every persona its own vocabulary and sentence length; no two lines "
        "may read as written by the same person.\n"
        if unique_messages
        else ""
    )
    return (
        f"Write exactly {persona_count} personas and exactly {step_count} steps.\n"
        f"speaker_id is 1..{persona_count} and indexes the roles array.\n"
        "reply_to_index is the 0-based index of an EARLIER step, or null. It may "
        "never point at this step or a later one.\n"
        "A step is either a reply or a reaction. For a reply set reaction to null "
        "and write text. For a reaction set text to the empty string, set "
        "reply_to_index to the step being reacted to, and set reaction to one of "
        f"{' '.join(get_args(NeuroshillingReaction))} and nothing else. At most one "
        "in four steps may be a reaction.\n"
        f"Every text is at most {_MAX_LINE_CHARS} characters.\n"
        "Write in the same language the topic is written in.\n"
        "Nobody names the product like an advertisement, nobody thanks the group, "
        "and no line contains a link.\n" + variety
    )


def build_prompt(
    topic: str,
    *,
    persona_count: int,
    step_count: int,
    unique_messages: bool,
    complaint: str | None = None,
) -> str:
    """Compose the generation prompt.

    ``complaint`` is what the previous attempt got wrong, fed back verbatim so the
    model repairs its own output instead of being asked the same question again.
    It is OUR validator's wording — never a provider or third-party exception
    string, which is why it is safe to interpolate.

    The literal word "json" appears below and must keep appearing: DeepSeek's JSON
    mode is documented as needing it in the prompt.
    """
    fenced = strip_fence_tags(topic).strip() or "(no topic given)"
    retry = "" if complaint is None else f"\nYour previous answer was rejected: {complaint}\n"
    return (
        "You script a short, natural-sounding group chat that several different "
        "people could plausibly have had in a Telegram group.\n\n"
        "The subject is the operator's own brief, between the <topic> markers:\n"
        f"<topic>\n{fenced}\n</topic>\n\n"
        f"{_rules(persona_count, step_count, unique_messages=unique_messages)}\n"
        "Answer with json and nothing else: no prose before or after it, no code "
        "fence. Two keys, roles and steps, exactly as in this example of the shape "
        f"(not the content):\n{_EXAMPLE}\n"
        f"{retry}"
    )
