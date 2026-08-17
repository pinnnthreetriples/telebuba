"""Contracts for the neuroshilling SCENARIO — roles, dialogue steps and the LLM draft.

Split from ``schemas.neuroshilling`` the same one-way round
``schemas.neurocomment_discovery_keywords`` is split from its parent: this module
imports the campaign vocabulary, and the campaign module must never import this
one. Roles and steps are edited on their own card and written by their own
endpoint, so nothing in the campaign contract refers to them.

Three shapes live here and they are deliberately not one:

* the INPUT models (``…Input``, ``NeuroshillingScenarioUpdate``) — what the
  operator's form may declare;
* the stored models (``NeuroshillingRole``, ``NeuroshillingStep``) — rows, with
  the ids and timestamps only the server mints;
* ``NeuroshillingDialogueDraft`` — what an LLM is allowed to hand back, which is
  neither of the other two and is validated before anything is written.

**Positions are ordinal, not data.** An input step carries no ``position``: its
place in the ``steps`` array IS its position, one-based, and the server derives
the column from it. That is what makes "positions are contiguous" unfalsifiable
rather than a rule somebody has to check. ``reply_to_position`` and
``target_position`` are one-based into that same array in both directions, so the
value a client reads back is the value it sends.

Every free-text field is bounded and every integer has ``ge``/``le``. These are
not tidiness: ``topic``, ``name`` and ``description`` are interpolated into an LLM
prompt, so an unbounded field is an unbounded prompt and an unbounded bill.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.neuroshilling import NeuroshillingScenarioStatus  # noqa: TC001 - runtime field type

NeuroshillingStepKind = Literal["message", "reaction"]

# The eight the mockup offers, and the only ones a step may carry. A non-Premium
# account gets exactly one reaction per message and custom emoji need Premium, so
# the set is fixed rather than free text — and being a ``Literal`` it reaches the
# generated TypeScript client as a union the picker can be built from.
NeuroshillingReaction = Literal["👍", "❤️", "🔥", "👏", "🤔", "💯", "✨", "🙌"]

# Hard ceilings on the WIRE. ``settings.neuroshilling.max_roles`` / ``max_steps``
# are the operator-tunable policy checked in the service; these are the bounds no
# configuration can raise, so an unbounded list can never reach the prompt builder
# or the database even if the settings file says otherwise.
MAX_ROLES = 10
MAX_STEPS = 50
MAX_ROLE_NAME = 60
# This IS the persona prompt, not a label — hence a paragraph rather than a line.
MAX_ROLE_DESCRIPTION = 1000
MAX_STEP_TEXT = 1000
MAX_STEP_DELAY_SECONDS = 3600
# A role reference on the wire is a client-chosen KEY, not necessarily a stored id
# (a freshly generated scenario has no ids yet). Bounded like everything else.
MAX_ROLE_KEY = 64

# Ceiling on what one generation may ask the model for. Well under ``MAX_STEPS``
# on purpose: ``schemas.gemini.GeminiRequest.max_output_tokens`` is capped at 2048
# and a Russian reply of ``MAX_STEP_TEXT`` characters costs several hundred tokens,
# so a twenty-step ask would be truncated mid-JSON — which the gateway reports as
# an error, i.e. the whole call fails rather than returning a short dialogue.
MAX_GENERATED_STEPS = 12


class NeuroshillingRoleInput(BaseModel):
    """One role as the form declares it.

    ``role_id`` is how a step says WHICH role speaks, and it is matched against the
    campaign's stored roles: a value that is already one of them updates that row
    in place — which is what keeps the account roster's ``role_id`` pointing
    somewhere after a save — and anything else (including ``None``) mints a new
    role. So a client may invent keys for roles that do not exist yet, and the
    server rewires the steps to the real ids it mints.
    """

    model_config = ConfigDict(extra="forbid")

    role_id: str | None = Field(default=None, max_length=MAX_ROLE_KEY)
    name: str = Field(min_length=1, max_length=MAX_ROLE_NAME)
    description: str = Field(default="", max_length=MAX_ROLE_DESCRIPTION)


class NeuroshillingStepInput(BaseModel):
    """One dialogue step as the form declares it — no ``position``, see the module docstring.

    Which of ``reply_to_position``, ``target_position`` and ``emoji`` a ``kind`` may
    carry is NOT checked here. ``services.neuroshilling.scenario._kind_field_problem``
    holds the write to it, so that the operator gets ``scenario_invalid`` — the refusal
    the page already has copy for — rather than the generic 422 a schema rule raises.
    """

    model_config = ConfigDict(extra="forbid")

    kind: NeuroshillingStepKind = "message"
    role_id: str | None = Field(default=None, max_length=MAX_ROLE_KEY)
    text: str = Field(default="", max_length=MAX_STEP_TEXT)
    reply_to_position: int | None = Field(default=None, ge=1, le=MAX_STEPS)
    target_position: int | None = Field(default=None, ge=1, le=MAX_STEPS)
    emoji: NeuroshillingReaction | None = None
    delay_min_seconds: int = Field(default=60, ge=0, le=MAX_STEP_DELAY_SECONDS)
    delay_max_seconds: int = Field(default=180, ge=0, le=MAX_STEP_DELAY_SECONDS)

    @model_validator(mode="after")
    def _check_delay_bounds(self) -> NeuroshillingStepInput:
        if self.delay_min_seconds > self.delay_max_seconds:
            msg = "delay_min_seconds must not exceed delay_max_seconds"
            raise ValueError(msg)
        return self


class NeuroshillingScenarioUpdate(BaseModel):
    """Roles and steps in ONE body, because they must be written in one transaction.

    Two endpoints would leave a window in which a step's ``role_id`` points at a
    role the other call has already deleted, and no amount of client ordering
    closes it.
    """

    model_config = ConfigDict(extra="forbid")

    roles: list[NeuroshillingRoleInput] = Field(default_factory=list, max_length=MAX_ROLES)
    steps: list[NeuroshillingStepInput] = Field(default_factory=list, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def _check_role_keys(self) -> NeuroshillingScenarioUpdate:
        """A key names ONE role. Two roles sharing one fork silently otherwise.

        The write matches the first occurrence against the stored row and inserts
        the second as a new role, and every step naming that key then follows
        whichever landed last — a dialogue nobody asked for, from a body nothing
        refused. ``None`` is not a key: keyless roles are minted, several at once.
        """
        keys = [role.role_id for role in self.roles if role.role_id is not None]
        if len(keys) != len(set(keys)):
            msg = "role_id must be unique across roles"
            raise ValueError(msg)
        return self


class NeuroshillingRole(BaseModel):
    """One row of ``neuroshilling_roles``."""

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    created_at: str = Field(min_length=1)


class NeuroshillingStep(BaseModel):
    """One row of ``neuroshilling_steps``.

    ``emoji`` is a plain ``str`` here rather than the input's ``Literal``: the
    column is free text and a row written before the set was narrowed must still
    read back instead of failing validation on the way out.
    """

    step_id: str = Field(min_length=1)
    position: int = Field(ge=1)
    kind: NeuroshillingStepKind
    role_id: str | None = None
    text: str = ""
    reply_to_position: int | None = None
    target_position: int | None = None
    emoji: str | None = None
    delay_min_seconds: int = 60
    delay_max_seconds: int = 180


class NeuroshillingScenario(BaseModel):
    """The scenario card's whole read: the dialogue plus whether it is approved.

    Carries no derived counters and no duration estimate — every one of those is
    an ``arr.length`` or a ``reduce`` over the two lists in this same payload.
    """

    campaign_id: str = Field(min_length=1)
    scenario_status: NeuroshillingScenarioStatus = "draft"
    roles: list[NeuroshillingRole] = Field(default_factory=list)
    steps: list[NeuroshillingStep] = Field(default_factory=list)


class NeuroshillingGenerateRequest(BaseModel):
    """Inputs to ONE generation. None of them is stored — they describe the ask.

    ``persona_count`` is the stepper on the scenario card; it never reached a
    column because it says nothing about a campaign once the dialogue exists.
    """

    model_config = ConfigDict(extra="forbid")

    # Two voices is the floor at which a dialogue is a dialogue at all.
    persona_count: int = Field(default=3, ge=2, le=MAX_ROLES)
    step_count: int = Field(default=8, ge=2, le=MAX_GENERATED_STEPS)


class NeuroshillingDraftRole(BaseModel):
    """One persona as the MODEL proposes it — no id, nothing stored yet."""

    name: str = Field(min_length=1, max_length=MAX_ROLE_NAME)
    description: str = Field(default="", max_length=MAX_ROLE_DESCRIPTION)


class NeuroshillingDraftStep(BaseModel):
    """One line of dialogue as the MODEL proposes it.

    ``speaker_id`` is one-based into ``NeuroshillingDialogueDraft.roles`` and
    ``reply_to_index`` is ZERO-based into ``steps`` — the model is asked for array
    indices because that is what it can count reliably, and the service converts
    both to the one-based positions the rest of the domain speaks.

    A non-null ``reaction`` is what makes the step a reaction rather than a reply.
    """

    speaker_id: int = Field(ge=1, le=MAX_ROLES)
    text: str = Field(default="", max_length=MAX_STEP_TEXT)
    reply_to_index: int | None = Field(default=None, ge=0, le=MAX_STEPS)
    reaction: NeuroshillingReaction | None = None


class NeuroshillingDialogueDraft(BaseModel):
    """What one LLM answer is allowed to be, before anything is written.

    Validated rather than trusted: the model is told the shape, and this is what
    holds it to it. Everything it can still get wrong inside a valid shape —
    a ``speaker_id`` past the roster, a ``reply_to_index`` pointing forwards — is
    checked by the service, which either re-asks with the complaint or repairs it.
    """

    model_config = ConfigDict(extra="ignore")

    roles: list[NeuroshillingDraftRole] = Field(default_factory=list, max_length=MAX_ROLES)
    steps: list[NeuroshillingDraftStep] = Field(default_factory=list, max_length=MAX_STEPS)
