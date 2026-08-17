"""Neuroshilling campaign policy — the page→repository seam.

Owns the rules the repository has no opinion about: which edits a running
campaign refuses, how the free-form target blob becomes a normalised list, and
which accounts the picker may still offer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.channel_tokens import parse_channels
from core.config import settings
from core.db import list_accounts, list_warming_account_ids
from core.repositories import neuroshilling as repository
from core.repositories.neurocomment import (
    get_listener_account_id,
    get_listener_running,
    list_active_campaign_account_names,
)
from schemas.neuroshilling import (
    NeuroshillingBoard,
    NeuroshillingBoardAccount,
    NeuroshillingRunStatus,
)
from services import _account_owner

# From the modules that own the answers, so the launch card and the engine cannot
# disagree: one owns "is this account still halted?", the other owns "does this
# campaign read its target chats?".
from services.neuroshilling._listen import enabled as listening_enabled
from services.neuroshilling._telegram import flood_since

if TYPE_CHECKING:
    from schemas.neuroshilling import (
        NeuroshillingBusyOwner,
        NeuroshillingCampaign,
        NeuroshillingCampaignCreate,
        NeuroshillingCampaignList,
        NeuroshillingCampaignUpdate,
        NeuroshillingRefusalCode,
    )

    _BusyMap = dict[str, tuple[NeuroshillingBusyOwner, str | None]]

# A campaign in either state has a run attached to it, so its shape is no longer
# the operator's to change.
_LIVE_STATUSES = frozenset({"running", "stopping"})

# The campaign fields whose value the approved dialogue was written FROM. Changing
# one of them means the reviewed text no longer answers the brief it was reviewed
# against — see :func:`_resets_approval`.
_APPROVAL_FIELDS = (
    "topic",
    "mode",
    "media_message_link",
    "media_step_position",
    "unique_messages",
    "use_chat_context",
)

# Named rather than written at the raise site so each one is greppable from the
# locale file that translates it.
_CAMPAIGN_RUNNING: NeuroshillingRefusalCode = "campaign_running"
_RUN_MODE_NOT_SUPPORTED: NeuroshillingRefusalCode = "run_mode_not_supported"
_TOO_MANY_TARGETS: NeuroshillingRefusalCode = "too_many_targets"
_UNKNOWN_ROLE: NeuroshillingRefusalCode = "unknown_role"


class NeuroshillingRefusedError(RuntimeError):
    """Base of the domain's refusals; ``code`` is what the SPA translates."""

    def __init__(self, code: NeuroshillingRefusalCode) -> None:
        super().__init__(code)
        self.code: NeuroshillingRefusalCode = code


class NeuroshillingConflictError(NeuroshillingRefusedError):
    """The campaign's current state forbids the request (HTTP 409)."""


class NeuroshillingInvalidError(NeuroshillingRefusedError):
    """The request describes a campaign this build cannot run (HTTP 400)."""


class NeuroshillingUnavailableError(NeuroshillingRefusedError):
    """An upstream provider could not serve the request (HTTP 503).

    503 rather than 502: ``api.errors`` has no description for 502, so
    ``error_responses(502)`` would raise at import time and the reachable-status
    contract test pins the set it does have.
    """


def parse_targets(targets_raw: str) -> list[str]:
    """Normalise the operator's target blob into deduplicated tokens, in order.

    Reuses ``core.channel_tokens.parse_channels``: the paste box is the same
    shape warming's is, and a second parser would be a second set of edge cases
    around invite links and query strings.
    """
    return parse_channels(targets_raw, max_length=settings.neuroshilling.max_target_length)


async def list_campaigns() -> NeuroshillingCampaignList:
    return await repository.list_campaigns()


async def create_campaign(data: NeuroshillingCampaignCreate) -> NeuroshillingCampaign:
    return await repository.create_campaign(data)


async def delete_campaign(campaign_id: str) -> bool:
    """Delete a campaign. ``False`` means there was none; a live run refuses."""
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return False
    refuse_while_live(campaign)
    await repository.delete_campaign(campaign_id)
    return True


async def update_campaign(
    campaign_id: str,
    data: NeuroshillingCampaignUpdate,
) -> NeuroshillingCampaign | None:
    """Apply the edited form. ``None`` means no such campaign."""
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    refuse_while_live(campaign)
    _check_shape(data)
    # Read once, not once per roster entry: this is a thread hop and a full table
    # read, and a twenty-account roster would otherwise pay for twenty of them.
    existing = await _existing_account_ids()
    data.accounts = [item for item in data.accounts if item.account_id in existing]
    await _check_roles(campaign_id, data)
    return await repository.update_campaign(
        campaign_id,
        data,
        reset_approval=_resets_approval(campaign, data),
    )


def _resets_approval(campaign: NeuroshillingCampaign, data: NeuroshillingCampaignUpdate) -> bool:
    """Does this edit invalidate an approval the operator already gave?

    One rule, and it is about MEANING rather than about which card the field sits
    on: a change to WHAT gets said resets the approval, a change to how fast or
    where it gets said does not. So the topic, the mode, the media the dialogue
    carries — the link AND which step carries it, since the approval gate checks
    both — whether every account writes its own wording, and whether the wording is
    written against what strangers in the target chat said all reset it, while the
    name, the target list, the roster, the pauses and every quota do not, since
    none of them changes a word of what was reviewed.

    Deliberately does NOT ask whether the campaign is approved right now. That
    read came from a fetch two awaits earlier, and an approval landing in the gap
    would survive the very edit that invalidated it. Writing ``draft`` whenever an
    approval field moved is idempotent on a campaign already in draft, and it
    closes THAT direction instead of narrowing it. The opposite one — this edit landing
    inside an approval's own window — is closed at the other end:
    ``scenario.approve_scenario`` writes only while the campaign still carries the
    ``updated_at`` its verdict was reached on, and the write below moves it.

    Enforced HERE and not in the UI. The generated client types every field, so a
    direct call could otherwise keep an approval alive across the exact edit it was
    given to guard against.
    """
    return any(getattr(campaign, field) != getattr(data, field) for field in _APPROVAL_FIELDS)


def refuse_while_live(campaign: NeuroshillingCampaign) -> None:
    """Public because the scenario module needs the same gate, from its own routes.

    Editing the dialogue under a run in flight is the one edit that cannot merely
    be inconsistent: the engine reads steps as it plays them.
    """
    if campaign.status in _LIVE_STATUSES:
        raise NeuroshillingConflictError(_CAMPAIGN_RUNNING)


def _check_shape(data: NeuroshillingCampaignUpdate) -> None:
    if data.run_mode == "parallel":
        # Refused on the SERVER, not merely hidden in the UI: the generated
        # TypeScript client types the field, so any direct call would otherwise
        # start a run mode this build has no engine for.
        raise NeuroshillingInvalidError(_RUN_MODE_NOT_SUPPORTED)
    if len(parse_targets(data.targets_raw)) > settings.neuroshilling.max_targets_per_campaign:
        raise NeuroshillingInvalidError(_TOO_MANY_TARGETS)


async def _check_roles(campaign_id: str, data: NeuroshillingCampaignUpdate) -> None:
    """Refuse a roster entry that names a role this campaign does not own.

    ``neuroshilling_accounts.role_id`` is a real foreign key, so an id belonging to
    another campaign — or to a role a concurrent save has just deleted — reaches
    the operator as a 500 instead of a refusal the page can render. Skipped
    entirely when no entry names a role, which is every stage-one request.
    """
    named = {item.role_id for item in data.accounts if item.role_id is not None}
    if named and not named <= await repository.list_campaign_role_ids(campaign_id):
        raise NeuroshillingInvalidError(_UNKNOWN_ROLE)


async def _existing_account_ids() -> set[str]:
    return {account.account_id for account in (await list_accounts()).accounts}


async def load_board(campaign_id: str) -> NeuroshillingBoard | None:
    """The whole page in one read. ``None`` means no such campaign."""
    campaign = await repository.fetch_campaign(campaign_id)
    if campaign is None:
        return None
    roster = {
        item.account_id: item for item in await repository.list_campaign_accounts(campaign_id)
    }
    busy = await _busy_owners(campaign_id)
    available: list[NeuroshillingBoardAccount] = []
    for account in (await list_accounts()).accounts:
        owner, holder_name = busy.get(account.account_id) or (None, None)
        # ONE list: the roster is a flag plus three fields overlaid on the pool
        # entry, so nothing downstream has to join two lists by account id.
        assigned = roster.get(account.account_id)
        available.append(
            NeuroshillingBoardAccount(
                account_id=account.account_id,
                title=account.label or account.account_id,
                assigned=assigned is not None,
                role_id=None if assigned is None else assigned.role_id,
                is_reserve=assigned is not None and assigned.is_reserve,
                state="active" if assigned is None else assigned.state,
                busy_owner=owner,
                busy_campaign_name=holder_name,
            ),
        )
    return NeuroshillingBoard(
        campaign=campaign,
        available=available,
        targets=parse_targets(campaign.targets_raw),
        run=await _run_status(campaign),
    )


async def run_status(campaign_id: str) -> NeuroshillingRunStatus | None:
    """The launch card's run block on its own. ``None`` means no such campaign."""
    campaign = await repository.fetch_campaign(campaign_id)
    return None if campaign is None else await _run_status(campaign)


async def _run_status(campaign: NeuroshillingCampaign) -> NeuroshillingRunStatus:
    """How far the current run has got, and who Telegram has taken out of it.

    The numerator counts delivered MESSAGE steps of this ``run_id`` and the
    denominator is targets x message steps. Reactions are journalled but appear in
    neither: a reaction is not a message, and a skipped one must not read as lost
    progress.

    A campaign with no ``run_id`` has no run to report on, so the numerator is zero
    without a query — the denominator still describes the work a Start would create,
    which is what the card shows before the first launch.

    ``halted_accounts`` is read through the same lens the join gate uses: a verdict
    about the ACCOUNT counts whichever campaign recorded it, and a flood counts only
    while it is still in force. Asking this campaign's presence rows alone left the
    card silent about accounts the engine will refuse to play.

    ``substitutions`` needs its own read because the board's roster carries ``state``
    but not ``replaced_by_account_id``, and those two disagree exactly when the reserve
    pool ran out.

    ``total`` is zero in ``revive`` mode because that run loops until it is stopped:
    there is no amount of work it is a fraction of, and a denominator describing one
    cycle would make a bar that fills up and then keeps going.

    ``listening`` is the campaign's three switches AND a run being in flight. Either
    half alone answers the wrong question — the switches are visible on the campaign
    row already, and a run being live says nothing about whether it reads.
    """
    _roles, steps = await repository.load_scenario(campaign.campaign_id)
    message_steps = sum(1 for step in steps if step.kind == "message")
    sent = (
        0 if campaign.run_id is None else await repository.count_sent_message_steps(campaign.run_id)
    )
    activity = await repository.count_chat_activity(campaign.campaign_id)
    return NeuroshillingRunStatus(
        status=campaign.status,
        run_id=campaign.run_id,
        sent=sent,
        total=(
            0
            if campaign.mode == "revive"
            else len(parse_targets(campaign.targets_raw)) * message_steps
        ),
        listening=campaign.status in _LIVE_STATUSES and listening_enabled(campaign),
        chat_messages_seen=activity.seen,
        human_replies_sent=activity.replied,
        # Counted over the whole campaign rather than the current run: the roster row a
        # substitution writes is the campaign's, and nothing clears it when a run ends.
        substitutions=await repository.count_substitutions(campaign.campaign_id),
        last_error_type=campaign.last_error,
        halted_accounts=await repository.list_halted_accounts(
            campaign.campaign_id,
            flood_since=flood_since(),
        ),
    )


async def _busy_owners(campaign_id: str) -> _BusyMap:
    """``account_id -> (owner, holder name)`` for every account something holds.

    Five sources, in rising authority — later passes overwrite earlier ones. The
    in-memory registry is the truth while a run is actually in flight, but it says
    nothing about a process that has only just started, so the durable rows back
    it up: a neuroshilling campaign still marked ``running``, a warming state, a
    serving neurocomment campaign, the running neurocomment listener. THIS campaign
    is excluded — its own roster is not "busy elsewhere", which is the only thing
    the picker is asking.

    The listener is here because ``_runtime._claim_accounts`` refuses a roster that
    carries it, and a card that showed it free would let the operator find that out
    only from the Start button. It is read the way that refusal reads it — running
    plus account — so the two cannot disagree about a PAUSED listener.
    """
    neuroshilling = await repository.list_running_campaign_account_names()
    busy: _BusyMap = {
        account_id: ("neurocomment", name)
        for account_id, name in (await list_active_campaign_account_names()).items()
    }
    listener = await get_listener_account_id() if await get_listener_running() else None
    if listener is not None:
        # The same owner as a serving campaign, because it IS neurocomment holding that
        # session; ``setdefault`` so an account doing both keeps the campaign's name,
        # which is the more specific of the two answers.
        busy.setdefault(listener, ("neurocomment", None))
    for account_id in await list_warming_account_ids():
        busy[account_id] = ("warming", None)
    for account_id, (holder_id, name) in neuroshilling.items():
        if holder_id != campaign_id:
            busy[account_id] = ("neuroshilling", name)
    for account_id, owner in _account_owner.owners().items():
        holder = _account_owner.holder_of(account_id)
        running = neuroshilling.get(account_id)
        if owner == "neuroshilling" and holder == campaign_id:
            # Held by THIS campaign, so not "busy elsewhere" — but only a NEUROSHILLING
            # marker is cleared. A warming or neurocomment hold is an unrelated
            # fact about the same account and dropping it would show it as free.
            if (busy.get(account_id) or (None, None))[0] == "neuroshilling":
                del busy[account_id]
            continue
        # A name is only knowable when the holder IS the campaign that owns it:
        # labelling a warming hold with whatever campaign happens to have the
        # account on a running roster names the wrong thing entirely.
        name = None
        if owner == "neuroshilling" and running is not None and running[0] == holder:
            name = running[1]
        busy[account_id] = (owner, name)
    return busy
