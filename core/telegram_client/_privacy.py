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

    ``account.getPrivacy`` returns the BASE rule (allow-all / allow-contacts /
    disallow-all) **plus** any per-user allow/disallow exception rules the
    account has. The base rule is what decides who can see the key, so the
    exception rules are skipped rather than making the whole key ``unknown``.
    An empty vector, a non-list, or a base rule we do not model (e.g.
    ``PrivacyValueAllowCloseFriends``) is reported as ``unknown``.
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
    """
    targets: tuple[tuple[object, PrivacyTarget | None], ...] = (
        (InputPrivacyKeyProfilePhoto(), action.profile_photo),
        (InputPrivacyKeyAbout(), action.bio),
        (InputPrivacyKeyStatusTimestamp(), action.last_seen),
    )
    for key, target in targets:
        if target is None:
            continue
        await client(SetPrivacyRequest(key=key, rules=[_INPUT_VALUES[target]()]))
