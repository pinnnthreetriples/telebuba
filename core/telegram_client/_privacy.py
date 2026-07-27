"""Account-privacy dispatch — ``account.getPrivacy`` / ``account.setPrivacy``.

Extracted-sibling pattern (see ``_read_channels.py``): ``_read.py`` keeps the
read match and ``_actions.py`` the write one, both importing from here. Errors
ride the existing ladders untouched — a read's FloodWait/RPCError becomes
``TelegramReadError`` in ``execute_read_many``, a write's is classified by
``execute`` — so there is no error translation and no retry logic in this module.

Rule vectors are inspected defensively, like the rest of the gateway: an
unrecognised or empty vector degrades to ``"unknown"`` instead of raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telethon.tl.functions.account import GetPrivacyRequest, SetPrivacyRequest
from telethon.tl.types import (
    InputPrivacyKeyAbout,
    InputPrivacyKeyProfilePhoto,
    InputPrivacyKeyStatusTimestamp,
    InputPrivacyValueAllowAll,
    InputPrivacyValueAllowContacts,
    InputPrivacyValueDisallowAll,
    PrivacyValueAllowAll,
    PrivacyValueAllowContacts,
    PrivacyValueDisallowAll,
)

from schemas.telegram_actions_privacy import PrivacySettingsResult

if TYPE_CHECKING:
    from telethon import TelegramClient

    from schemas.telegram_actions import SetPrivacySettings
    from schemas.telegram_actions_privacy import PrivacyLevel, PrivacyTarget

# Target level -> the ``InputPrivacyValue*`` constructor that expresses it.
_INPUT_VALUES: dict[str, type] = {
    "everybody": InputPrivacyValueAllowAll,
    "contacts": InputPrivacyValueAllowContacts,
    "nobody": InputPrivacyValueDisallowAll,
}


def _level_from_rules(rules: object) -> PrivacyLevel:
    """Collapse Telegram's rule vector onto the one level the UI shows.

    ``account.getPrivacy`` returns a BASE rule (allow-all / allow-contacts /
    disallow-all) plus any narrowing rules the account carries. Only the base
    rule is reported; everything else is skipped rather than making the whole
    key ``unknown``. ``unknown`` is therefore reached by an empty vector, a
    non-list, or a vector with no base rule at all.

    Two consequences to know before trusting the answer:

    - The collapse is LOSSY and the loss is one-directional. Telethon 1.44 has
      twelve ``PrivacyValue*`` types; the skipped ones include per-user
      ``AllowUsers``/``DisallowUsers`` but also ``DisallowContacts``,
      ``AllowPremium``, ``AllowCloseFriends``, ``AllowBots`` and the chat-
      participant pair. So "everybody except these two accounts" reports
      ``everybody`` — a false all-clear when the operator is asking why those
      two accounts cannot see the avatar. The exceptions are visible only in a
      Telegram client, or by dumping this vector raw, which the dashboard does
      not yet do.
    - Iteration returns on the FIRST base rule matched, so a restrictive key
      that also grants a narrower audience — ``[AllowCloseFriends(),
      DisallowAll()]`` — reports ``nobody``, not ``unknown``. For the question
      the dashboard exists to answer (can a stranger see this) that is the
      right answer, but it is not what "we do not model it" would suggest.
    """
    if not isinstance(rules, list):
        return "unknown"
    for rule in rules:
        if isinstance(rule, PrivacyValueAllowAll):
            return "everybody"
        if isinstance(rule, PrivacyValueAllowContacts):
            return "contacts"
        if isinstance(rule, PrivacyValueDisallowAll):
            return "nobody"
    return "unknown"


async def dispatch_get_privacy_settings(client: TelegramClient) -> PrivacySettingsResult:
    """Read the three keys that decide whether strangers see avatar / bio / last seen.

    Three separate calls: ``account.getPrivacy`` takes exactly one key and
    Telegram offers no batch form.
    """
    photo = await client(GetPrivacyRequest(key=InputPrivacyKeyProfilePhoto()))
    about = await client(GetPrivacyRequest(key=InputPrivacyKeyAbout()))
    status = await client(GetPrivacyRequest(key=InputPrivacyKeyStatusTimestamp()))
    return PrivacySettingsResult(
        profile_photo=_level_from_rules(getattr(photo, "rules", None)),
        bio=_level_from_rules(getattr(about, "rules", None)),
        last_seen=_level_from_rules(getattr(status, "rules", None)),
    )


async def dispatch_set_privacy_settings(
    client: TelegramClient,
    action: SetPrivacySettings,
) -> None:
    """One ``account.setPrivacy`` per non-``None`` field; ``None`` fields are not sent.

    ``setPrivacy`` REPLACES a key's whole rule vector, so any per-user exception
    on that key is dropped. Deliberate: the dashboard exposes the three coarse
    levels only, and a "contacts plus exceptions" state is exactly what made the
    avatar invisible to strangers in the first place.

    There is no rollback (never auto-undo — repo data-safety rule), so a refusal on
    the second key leaves the first already changed on Telegram. The names that DID
    land are attached to the escaping exception as ``privacy_applied`` and the
    executor threads them into the ``ActionResult`` — the same ``getattr`` contract
    ``ChannelGatewayError.channel_id`` already rides. The fleet-wide apply has no
    other way to learn it, and reporting such an account as plainly ``failed`` told
    the operator it was untouched while its avatar was already public.
    """
    targets: tuple[tuple[object, PrivacyTarget | None, str], ...] = (
        (InputPrivacyKeyProfilePhoto(), action.profile_photo, "profile_photo"),
        (InputPrivacyKeyAbout(), action.bio, "bio"),
        (InputPrivacyKeyStatusTimestamp(), action.last_seen, "last_seen"),
    )
    applied: list[str] = []
    for key, target, name in targets:
        if target is None:
            continue
        try:
            await client(SetPrivacyRequest(key=key, rules=[_INPUT_VALUES[target]()]))
        except Exception as exc:
            # Annotated and re-raised untouched, so ``execute``'s flood / frozen /
            # dead-session ladders still classify it exactly as before.
            exc.privacy_applied = list(applied)  # ty: ignore[unresolved-attribute]
            raise
        applied.append(name)
