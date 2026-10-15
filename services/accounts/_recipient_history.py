"""Recipient history rules for a bulk-message run: no repeat, one client — one account.

Judged once, when the run is stored, against ``recipient_history``: a recipient any
account wrote to within ``no_repeat_days`` is skipped (``recently_messaged``); with
``one_account_per_client`` a recipient someone wrote to before belongs to the
account that wrote first. That account gets them again when it is in the run
(split mode pins them to it, so a stop or a full day never hands them on); when
it is not, they are skipped (``owned_by_other_account``) rather than silently
switched to another account.

A recipient is matched by its Telegram key (user id or username) or by the phone
a lookup found it by, so a client found again by phone is known even when this
run writes to them under a different key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.repositories.recipient_history import add_recipient_contact, find_recipient_contacts
from schemas.recipient_history import RecipientContact
from schemas.recipient_rules import RecipientRules

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from schemas.bulk_messages import BulkMessageRequest
    from schemas.bulk_runs import BulkRunItem

RECENTLY_MESSAGED = "recently_messaged"
OWNED_BY_OTHER_ACCOUNT = "owned_by_other_account"


def phone_key(phone: str | None) -> str | None:
    """Digits only, so "+7 999…" and "7999…" are one client; ``None`` without digits."""
    digits = "".join(char for char in phone or "" if char.isdigit())
    return digits or None


@dataclass(frozen=True)
class Verdict:
    # Why the recipient is skipped; ``None`` when they may be written to.
    skip: str | None = None
    # The account the recipient belongs to, when it is in the run.
    owner: str | None = None


def _judge(
    data: BulkMessageRequest,
    recipients: Sequence[tuple[str, str]],
    contacts: Sequence[RecipientContact],
    now: datetime,
) -> dict[str, Verdict]:
    """A verdict for each recipient with history; ``contacts`` come oldest first."""
    rules = data.recipient_rules or RecipientRules()
    verdicts: dict[str, Verdict] = {}
    for recipient, peer in recipients:
        key, phone = peer.casefold(), phone_key(data.recipient_phones.get(recipient))
        mine = [c for c in contacts if c.peer == key or (phone and c.phone == phone)]
        if not mine:
            continue
        last = datetime.fromisoformat(mine[-1].sent_at)
        if rules.no_repeat_days is not None and now - last < timedelta(days=rules.no_repeat_days):
            verdicts[recipient] = Verdict(skip=RECENTLY_MESSAGED)
        elif rules.one_account_per_client:
            owner = mine[0].account_id
            verdicts[recipient] = (
                Verdict(owner=owner)
                if owner in data.account_ids
                else Verdict(skip=OWNED_BY_OTHER_ACCOUNT)
            )
    return verdicts


async def screen_recipients(
    data: BulkMessageRequest,
    recipients: Sequence[tuple[str, str]],
    now: datetime,
) -> tuple[BulkMessageRequest, dict[str, Verdict]]:
    """Judge the run's recipients; split mode pins each owned one to its account.

    The owner outranks the account a lookup pinned a raw user id to: it wrote to
    the client before, so it can address them, and the client stays with it.
    """
    phones = [key for key in map(phone_key, data.recipient_phones.values()) if key]
    contacts = await find_recipient_contacts([peer.casefold() for _, peer in recipients], phones)
    verdicts = _judge(data, recipients, contacts, now)
    if data.mode != "split":
        return data, verdicts
    owned = {r: v.owner for r, v in verdicts.items() if v.owner is not None}
    return data.model_copy(
        update={"recipient_accounts": {**data.recipient_accounts, **owned}}
    ), verdicts


def skip_by_history(items: Sequence[BulkRunItem], verdicts: Mapping[str, Verdict]) -> None:
    """Mark planned sends the history rules refuse; they are final before the run starts."""
    for item in items:
        verdict = verdicts.get(item.recipient)
        if verdict is None:
            continue
        if verdict.skip is not None:
            item.status, item.error_code = "skipped", verdict.skip
        elif verdict.owner is not None and item.account_id != verdict.owner:
            # Each mode: only the owner writes to its client again.
            item.status, item.error_code = "skipped", OWNED_BY_OTHER_ACCOUNT


async def record_first_message(item: BulkRunItem, phone: str | None, sent_at: datetime) -> None:
    """Remember that ``item``'s account may have written to its recipient."""
    await add_recipient_contact(
        RecipientContact(
            peer=item.peer.casefold(),
            phone=phone_key(phone),
            account_id=item.account_id,
            sent_at=sent_at.isoformat(),
        )
    )
