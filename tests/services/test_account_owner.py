"""The cross-feature account ownership registry.

Everything here is about ONE property: a claim is released by the identity that
took it and by nobody else. That is what stops a late done-callback from an
evicted generation freeing an account its successor is already using.
"""

from __future__ import annotations

import pytest

from services import _account_owner


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    _account_owner.reset_for_tests()


def test_an_unclaimed_account_has_no_owner() -> None:
    assert _account_owner.owner_of("acc-1") is None
    assert _account_owner.holder_of("acc-1") is None
    assert _account_owner.owners() == {}


def test_a_claim_names_its_owner_and_its_holder() -> None:
    assert _account_owner.try_claim("acc-1", "warming", "run-7") is None

    assert _account_owner.owner_of("acc-1") == "warming"
    assert _account_owner.holder_of("acc-1") == "run-7"
    assert _account_owner.owners() == {"acc-1": "warming"}


def test_reclaiming_with_the_same_identity_is_idempotent() -> None:
    """A caller re-entering its own claim path must not be refused by itself."""
    _account_owner.try_claim("acc-1", "neuroshilling", "camp-1")

    assert _account_owner.try_claim("acc-1", "neuroshilling", "camp-1") is None
    assert _account_owner.holder_of("acc-1") == "camp-1"


def test_a_second_owner_is_refused_and_told_who_holds_it() -> None:
    _account_owner.try_claim("acc-1", "warming", "run-7")

    assert _account_owner.try_claim("acc-1", "neuroshilling", "camp-1") == "warming"
    assert _account_owner.holder_of("acc-1") == "run-7"


def test_a_second_campaign_cannot_claim_a_held_account() -> None:
    """One account may be ASSIGNED to many campaigns but held by only one run."""
    _account_owner.try_claim("acc-1", "neuroshilling", "camp-1")

    assert _account_owner.try_claim("acc-1", "neuroshilling", "camp-2") == "neuroshilling"
    assert _account_owner.holder_of("acc-1") == "camp-1"


def test_release_needs_both_the_owner_and_the_holder_to_match() -> None:
    """A stale done-callback from an evicted generation must not free the account."""
    _account_owner.try_claim("acc-1", "warming", "run-7")

    _account_owner.release("acc-1", "warming", "run-6")
    assert _account_owner.owner_of("acc-1") == "warming"

    _account_owner.release("acc-1", "neuroshilling", "run-7")
    assert _account_owner.owner_of("acc-1") == "warming"

    _account_owner.release("acc-1", "warming", "run-7")
    assert _account_owner.owner_of("acc-1") is None


def test_releasing_an_unclaimed_account_is_a_no_op() -> None:
    _account_owner.release("acc-1", "warming", "run-7")

    assert _account_owner.owners() == {}


def test_release_owner_clears_only_its_own_slice() -> None:
    """Startup reconciliation wipes its own claims and must not touch the other's."""
    _account_owner.try_claim("acc-1", "warming", "run-7")
    _account_owner.try_claim("acc-2", "neuroshilling", "camp-1")
    _account_owner.try_claim("acc-3", "neuroshilling", "camp-2")

    _account_owner.release_owner("neuroshilling")

    assert _account_owner.owners() == {"acc-1": "warming"}


def test_the_snapshot_does_not_alias_the_registry() -> None:
    _account_owner.try_claim("acc-1", "warming", "run-7")
    snapshot = _account_owner.owners()

    _account_owner.release("acc-1", "warming", "run-7")

    assert snapshot == {"acc-1": "warming"}
