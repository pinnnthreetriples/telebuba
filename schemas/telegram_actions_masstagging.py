"""Masstagging Telegram actions — the auto-delete toggle, and who a target is.

A sibling of :mod:`schemas.telegram_actions` for the file-size cap; the unions there
re-import every name. The write shares the ``masstag_`` prefix, which is how
``core.telegram_client._action_families`` routes it without a new dispatcher arm. The
story itself is the shared ``PostStory`` with its mentions and link.

A user carries what the run needs and nothing more: no phone and no ``access_hash``,
both being per-account secrets of the session that saw them. An id resolved by one
account is only usable by that account — the gateway finds it in its own session.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The one auto-delete period every client offers (one day); 0 switches it off again.
HISTORY_TTL_DAY = 86_400
# Telegram's page ceiling for a member list.
MEMBERS_PAGE_MAX = 200
CONTACTS_MAX = 5000


class MasstagToggleTtl(BaseModel):
    """Turn the chat's auto-delete on for a moment and off again.

    The person sees two service lines in the chat with this account — that is the whole
    point (priming), and it also opens the dialog before a story mention. ``peer`` is a
    decimal user id THIS account resolved, or a public username.
    """

    action_type: Literal["masstag_toggle_ttl"] = "masstag_toggle_ttl"
    peer: str = Field(min_length=1, max_length=64)
    period_seconds: int = Field(default=HISTORY_TTL_DAY, gt=0)
    # How long the timer stays on before it is switched off again.
    hold_seconds: float = Field(default=2.0, ge=0.0, le=10.0)


class ResolveMasstagTarget(BaseModel):
    """Read-only: who a target is, as this account sees it.

    ``target`` is a username without ``@`` or a phone in ``+digits`` form. A phone is
    found only among this account's contacts — it is never imported. A group is
    answered with up to ``members_limit`` of its members.
    """

    action_type: Literal["resolve_masstag_target"] = "resolve_masstag_target"
    target: str = Field(min_length=1, max_length=64)
    members_limit: int = Field(default=MEMBERS_PAGE_MAX, ge=0, le=MEMBERS_PAGE_MAX)


class ListMasstagContacts(BaseModel):
    """Read-only: this account's contacts, for the "contacts of the accounts" source."""

    action_type: Literal["list_masstag_contacts"] = "list_masstag_contacts"
    limit: int = Field(default=CONTACTS_MAX, ge=1, le=CONTACTS_MAX)


class MasstagUser(BaseModel):
    user_id: int
    username: str | None = None
    first_name: str = ""
    is_bot: bool = False
    is_deleted: bool = False


MasstagResolveOutcome = Literal[
    # A person: ``user`` is set.
    "user",
    # A group: ``members`` holds the people found in it.
    "group",
    # The group hides its member list.
    "hidden",
    # A channel, or anything else that is not a person or a group.
    "not_user",
    "not_found",
]


class MasstagResolvedTarget(BaseModel):
    outcome: MasstagResolveOutcome
    user: MasstagUser | None = None
    members: list[MasstagUser] = Field(default_factory=list)


class MasstagContactsResult(BaseModel):
    users: list[MasstagUser]
