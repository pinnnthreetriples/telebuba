"""Neurocomment *request* schemas — split from ``schemas.neurocomment`` for the file-size budget.

Data contract only, no behaviour (non-negotiable #2). These are the inbound bodies
of the ``api/v1/neurocomment`` routes — operator intent travelling in, never a read
model travelling out — which is why they group cleanly away from the campaign/board
schemas left behind. Re-exported from ``schemas.neurocomment`` so
``from schemas.neurocomment import LinkChannelRequest`` etc. keep working unchanged.
Self-contained: depends only on pydantic + stdlib typing, so ``schemas.neurocomment``
imports these back without a cycle.

``CampaignCreate`` deliberately stayed behind: it is the one request body typed on
``CampaignStatus``, the campaign-lifecycle vocabulary the read models share and the
module docstring there documents. ``CampaignRunStatus`` did move, because it exists
solely for ``SetCampaignStatusRequest``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LinkChannelRequest(BaseModel):
    """Attach a channel to a campaign (the campaign id is the route path param)."""

    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1)

    @model_validator(mode="after")
    def _canonical_handle(self) -> LinkChannelRequest:
        """Strip the decorative ``@`` so the stored handle is canonical.

        ``@News`` and ``news`` are ONE Telegram channel — they resolve to a single
        peer id — so accepting the sigil verbatim let the operator link the same
        channel to a second campaign, and the listener maps only the last spelling
        it saw: the other campaign's link went silently dead. Discovery adopt already
        writes the bare handle; this holds the hand-typed box to the same standard.
        Letter case is left alone (Telegram's own casing is unknowable here) — the DB
        fold makes reads and the unique index case-insensitive.
        """
        canonical = self.channel.strip().lstrip("@")
        if not canonical:
            msg = "channel must be a handle, not only '@' or whitespace"
            raise ValueError(msg)
        self.channel = canonical
        return self


class AssignAccountRequest(BaseModel):
    """Assign an account to a campaign (the campaign id is the route path param)."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)


class SolverToggleRequest(BaseModel):
    """Turn the per-campaign challenge (captcha) solver on/off."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool


class UpdatePromptRequest(BaseModel):
    """Replace a campaign's generation prompt (the edit-prompt modal)."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)


class RetryPairRequest(BaseModel):
    """Operator retry of one (account, channel) challenge — the captcha «Решить»."""

    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=1)
    channel: str = Field(min_length=1)


class StartNeurocommentRequest(BaseModel):
    """Start the fleet listener on the given account."""

    model_config = ConfigDict(extra="forbid")

    listener_account_id: str = Field(min_length=1)


# The operator play/pause button toggles between the running and paused states;
# ``archived`` is a separate retire action, not part of per-campaign run/pause.
CampaignRunStatus = Literal["active", "paused"]


class SetCampaignStatusRequest(BaseModel):
    """Per-campaign run/pause: flip a campaign between ``active`` and ``paused`` (#148)."""

    model_config = ConfigDict(extra="forbid")

    status: CampaignRunStatus


class SetAccountChannelRequest(BaseModel):
    """Set the campaign channels an account targets; an empty list = all channels."""

    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(default_factory=list)
