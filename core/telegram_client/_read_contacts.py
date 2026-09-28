"""Read-only contact-lookup dispatcher — phone numbers to Telegram users.

Extracted-sibling pattern (see ``_read_discovery.py``): ``_read.py`` keeps the
match and imports this dispatcher. Errors ride the ``execute_read_many`` ladder
untouched (FloodWait family / RPC -> ``TelegramReadError``).

``contacts.ImportContacts`` is the only method that resolves a *batch* of phones
to users, and it saves each found user as a contact on the account. The warming
DM path (``_dm.py``) deliberately avoids that with ``ResolvePhone``; this operator
tool keeps the contacts on purpose, so no ``DeleteContacts`` follows.

Every Telethon attribute is read through ``getattr(..., default)``, matching the
rest of the gateway — a layer change upstream (or a MagicMock in a test) then
yields an empty field instead of raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon import utils
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.types import InputPhoneContact

from schemas.telegram_actions_contacts import ContactLookupBatchResult, ContactLookupMatch

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import LookupContactsByPhone


def _clean(value: object) -> str | None:
    """One Telethon text field, trimmed to ``None`` when empty or non-string."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


async def dispatch_lookup_contacts_by_phone(
    client: TelegramClient,
    action: LookupContactsByPhone,
) -> ContactLookupBatchResult:
    """Resolve a batch of phones via ``contacts.ImportContacts``.

    ``client_id`` on each ``InputPhoneContact`` is the phone's index in the batch;
    the reply's ``imported`` echoes it back, which is how a found ``user_id`` is
    tied to the phone that produced it (the reply gives no phone). ``parse_phone``
    strips ``+()- `` and validates digits — an unparseable entry never reaches the
    RPC and is reported unresolved, so one bad number cannot fail the whole batch.

    A found contact is saved on the account by design (the operator keeps them);
    the ``first_name`` we file is the phone itself, so the saved contact is
    self-identifying rather than a blank row.
    """
    by_client_id: dict[int, str] = {}
    contacts: list[InputPhoneContact] = []
    unresolved: list[str] = []
    for index, raw in enumerate(action.phones):
        parsed = utils.parse_phone(raw)
        if parsed is None:
            unresolved.append(raw)
            continue
        by_client_id[index] = raw
        contacts.append(
            InputPhoneContact(client_id=index, phone=parsed, first_name=raw, last_name="")
        )
    if not contacts:
        return ContactLookupBatchResult(matches=[], unresolved=unresolved)

    result = await client(ImportContactsRequest(contacts=contacts))

    users_by_id: dict[int, object] = {
        user_id: user
        for user in getattr(result, "users", None) or []
        if isinstance((user_id := getattr(user, "id", None)), int)
    }
    matched: set[int] = set()
    matches: list[ContactLookupMatch] = []
    for imported in getattr(result, "imported", None) or []:
        client_id = getattr(imported, "client_id", None)
        user_id = getattr(imported, "user_id", None)
        if not isinstance(client_id, int) or not isinstance(user_id, int):
            continue
        phone = by_client_id.get(client_id)
        if phone is None:
            continue
        matched.add(client_id)
        user = users_by_id.get(user_id)
        matches.append(
            ContactLookupMatch(
                phone=phone,
                user_id=user_id,
                username=_clean(getattr(user, "username", None)),
                first_name=_clean(getattr(user, "first_name", None)),
                last_name=_clean(getattr(user, "last_name", None)),
            )
        )
    unresolved.extend(
        phone for client_id, phone in by_client_id.items() if client_id not in matched
    )
    return ContactLookupBatchResult(matches=matches, unresolved=unresolved)
