"""Contact-lookup read action — split from ``schemas.telegram_actions``.

Resolve phone numbers to Telegram users via ``contacts.ImportContacts`` (the only
batch ``phone -> user`` method Telegram exposes to an ordinary account). One
dispatch is one ``ImportContacts`` RPC over a batch of phones; the service layer
chunks the operator's full list into batches and spreads them across accounts.

Unlike the warming DM path (``core.telegram_client._dm`` uses ``ResolvePhone`` so
it never saves a contact and never builds a correlated fleet-contact graph), this
operator tool DELIBERATELY keeps the found users as contacts on the account — it
is the operator's explicit choice, and ``ImportContacts`` saves them regardless.

Names are imported back into ``schemas.telegram_actions`` so callers keep using
``from schemas.telegram_actions import LookupContactsByPhone``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# One ``ImportContacts`` RPC carries at most this many phones. The service batches
# the operator's list to this size; the action caps it so a malformed request
# cannot ask the gateway to build an unbounded vector.
CONTACT_LOOKUP_MAX_BATCH = 200


class LookupContactsByPhone(BaseModel):
    """Read-only intent: which of these phones belong to a Telegram account.

    ``phones`` are already normalized and de-duplicated by the service; the
    gateway parses each with Telethon's own ``parse_phone`` before the RPC and
    reports anything unparseable as unresolved rather than failing the batch.
    """

    action_type: Literal["lookup_contacts_by_phone"] = "lookup_contacts_by_phone"
    phones: list[str] = Field(min_length=1, max_length=CONTACT_LOOKUP_MAX_BATCH)


class ContactLookupMatch(BaseModel):
    """One phone that resolved to a Telegram account.

    ``phone`` is echoed back exactly as the gateway received it so the service
    can map the match onto the operator's original entry. A user with no public
    ``username`` is addressable only by ``user_id`` from the account that found
    it (its session learned the access hash), which the send step accounts for.
    """

    phone: str
    user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None


class ContactLookupBatchResult(BaseModel):
    """Gateway output for one lookup batch: who resolved, who did not, who was deferred.

    ``unresolved`` holds every input phone that did not resolve — unparseable, not
    registered, or hidden by the target's "who can find me by phone" privacy.
    ``retry`` holds the phones Telegram's ``retry_contacts`` deferred: the import limit
    answers with this list instead of a FloodWait, so those were never checked.
    """

    matches: list[ContactLookupMatch]
    unresolved: list[str]
    retry: list[str] = Field(default_factory=list)
