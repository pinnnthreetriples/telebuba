"""Account-privacy service tests — per-account read/apply and the fleet-wide apply."""

from __future__ import annotations

import asyncio

import pytest

from core.db import create_account, update_account_status
from core.telegram_client import TelegramAccountNotFoundError, TelegramReadError
from schemas.accounts import AccountCreate
from schemas.privacy import AccountPrivacyUpdateRequest
from schemas.telegram_actions import ActionResult
from schemas.telegram_actions_privacy import PrivacySettingsResult, SetPrivacySettings
from services.accounts import (
    AccountActionError,
    AccountNotFoundError,
    apply_account_privacy,
    apply_privacy_to_all_accounts,
    read_account_privacy,
)


def _ok(account_id: str) -> ActionResult:
    return ActionResult(status="ok", action_type="set_privacy_settings", account_id=account_id)


def _levels(**overrides: str) -> PrivacySettingsResult:
    return PrivacySettingsResult(**overrides)  # ty: ignore[invalid-argument-type]


@pytest.mark.asyncio
async def test_read_account_privacy_returns_the_live_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-read"))
    read_calls: list[object] = []

    async def _fake_read(account_id: str, action: object) -> PrivacySettingsResult:
        assert account_id == "acc-read"
        read_calls.append(action)
        return _levels(profile_photo="contacts", bio="nobody", last_seen="everybody")

    monkeypatch.setattr("services.accounts.privacy.execute_read", _fake_read)

    view = await read_account_privacy("acc-read")

    assert view.error is None
    assert view.settings is not None
    assert view.settings.profile_photo == "contacts"
    assert view.settings.bio == "nobody"
    assert view.settings.last_seen == "everybody"
    assert len(read_calls) == 1


@pytest.mark.asyncio
async def test_read_account_privacy_reports_a_refused_read_in_the_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same idiom as ``AccountProfileView``: a refusal is data, not an exception."""
    await create_account(AccountCreate(account_id="acc-flood"))

    async def _boom(account_id: str, action: object) -> PrivacySettingsResult:  # noqa: ARG001
        reason = "FloodWait(30s)"
        raise TelegramReadError(reason)

    monkeypatch.setattr("services.accounts.privacy.execute_read", _boom)

    view = await read_account_privacy("acc-flood")

    assert view.settings is None
    assert view.error == "FloodWait(30s)"


@pytest.mark.asyncio
async def test_read_account_privacy_unknown_account_raises_not_found() -> None:
    with pytest.raises(AccountNotFoundError):
        await read_account_privacy("acc-missing")


@pytest.mark.asyncio
async def test_apply_account_privacy_sends_only_the_named_keys_and_re_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-apply"))
    actions: list[SetPrivacySettings] = []
    reads: list[str] = []

    async def _fake_execute(account_id: str, action: SetPrivacySettings) -> ActionResult:
        actions.append(action)
        return _ok(account_id)

    async def _fake_read(account_id: str, action: object) -> PrivacySettingsResult:  # noqa: ARG001
        reads.append(account_id)
        return _levels(profile_photo="everybody", bio="everybody")

    monkeypatch.setattr("services.accounts.privacy.execute", _fake_execute)
    monkeypatch.setattr("services.accounts.privacy.execute_read", _fake_read)

    view = await apply_account_privacy(
        "acc-apply",
        AccountPrivacyUpdateRequest(profile_photo="everybody", bio="everybody"),
    )

    assert [(a.profile_photo, a.bio, a.last_seen) for a in actions] == [
        ("everybody", "everybody", None),
    ]
    # The fresh state comes from a re-read, not from echoing the request.
    assert reads == ["acc-apply"]
    assert view.settings is not None
    assert view.settings.profile_photo == "everybody"


@pytest.mark.asyncio
async def test_apply_account_privacy_raises_on_a_refused_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_account(AccountCreate(account_id="acc-refused"))

    async def _refused(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        # The privacy dispatcher maps no Telethon refusal to a stable code, so a
        # refused write arrives as an unmapped RPCError carrying English prose.
        return ActionResult(
            status="failed",
            action_type="set_privacy_settings",
            account_id=account_id,
            error_type="RPCError",
            error_message="PRIVACY_VALUE_INVALID",
        )

    monkeypatch.setattr("services.accounts.privacy.execute", _refused)

    with pytest.raises(AccountActionError, match=r"^failed$"):
        await apply_account_privacy("acc-refused", AccountPrivacyUpdateRequest(bio="everybody"))


@pytest.mark.asyncio
async def test_apply_account_privacy_unknown_account_raises_not_found() -> None:
    with pytest.raises(AccountNotFoundError):
        await apply_account_privacy("acc-missing", AccountPrivacyUpdateRequest(bio="everybody"))


def test_all_none_update_request_is_rejected() -> None:
    """A body that changes nothing must not cost a Telegram round trip."""
    with pytest.raises(ValueError, match="at least one of"):
        AccountPrivacyUpdateRequest()


@pytest.mark.asyncio
async def test_apply_privacy_to_all_accounts_counts_ok_failed_and_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only permanently dead accounts are skipped; failures never abort the sweep.

    ``acc-new`` and ``acc-flood`` are the regression this pins. Filtering on
    ``health_for_status(...) == "ok"`` skipped both, which meant a farm imported
    minutes ago — every row still ``new`` — reported ``ok: 0`` on exactly the
    fleet the operator was trying to open up.
    """
    for account_id in ("acc-alive", "acc-broken", "acc-frozen", "acc-new", "acc-flood"):
        await create_account(AccountCreate(account_id=account_id))
    await update_account_status("acc-alive", status="alive")
    await update_account_status("acc-broken", status="alive")
    await update_account_status("acc-frozen", status="frozen")
    await update_account_status("acc-flood", status="flood_wait")
    # "acc-new" keeps its default ``new`` status — untested, not dead.

    attempted: list[str] = []

    async def _fake_execute(account_id: str, action: SetPrivacySettings) -> ActionResult:
        attempted.append(account_id)
        assert action.profile_photo == "everybody"
        if account_id == "acc-broken":
            return ActionResult(
                status="failed",
                action_type="set_privacy_settings",
                account_id=account_id,
                error_type="RPCError",
                error_message="PRIVACY_VALUE_INVALID",
            )
        return _ok(account_id)

    monkeypatch.setattr("services.accounts.privacy.execute", _fake_execute)

    result = await apply_privacy_to_all_accounts(
        AccountPrivacyUpdateRequest(profile_photo="everybody"),
    )

    assert sorted(attempted) == ["acc-alive", "acc-broken", "acc-flood", "acc-new"]
    assert (result.ok, result.failed, result.skipped) == (3, 1, 1)
    by_id = {outcome.account_id: outcome for outcome in result.outcomes}
    assert by_id["acc-alive"].status == "ok"
    assert by_id["acc-new"].status == "ok"
    assert by_id["acc-flood"].status == "ok"
    assert by_id["acc-broken"].status == "failed"
    # Bounded code, never the RPCError prose (see AccountPrivacyOutcome.error).
    assert by_id["acc-broken"].error == "failed"
    # A skip names the status that caused it — a bare count reads to the operator
    # as "your sessions are broken", which for `new` or `flood_wait` is false.
    assert by_id["acc-frozen"].status == "skipped"
    assert by_id["acc-frozen"].error == "frozen"


@pytest.mark.asyncio
async def test_apply_privacy_to_all_accounts_collects_an_unexpected_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exploding account is reported, not propagated — the sweep still finishes."""
    for account_id in ("acc-one", "acc-two"):
        await create_account(AccountCreate(account_id=account_id))
        await update_account_status(account_id, status="alive")

    async def _fake_execute(account_id: str, action: object) -> ActionResult:  # noqa: ARG001
        if account_id == "acc-one":
            msg = "pool exploded"
            raise RuntimeError(msg)
        return _ok(account_id)

    monkeypatch.setattr("services.accounts.privacy.execute", _fake_execute)

    result = await apply_privacy_to_all_accounts(AccountPrivacyUpdateRequest(bio="nobody"))

    assert (result.ok, result.failed, result.skipped) == (1, 1, 0)
    failed = next(outcome for outcome in result.outcomes if outcome.status == "failed")
    assert failed.account_id == "acc-one"
    # The class name, not the message: the message is arbitrary text that can
    # carry a proxy host or a session path, and this value is an API response.
    assert failed.error == "RuntimeError"


@pytest.mark.asyncio
async def test_apply_privacy_to_all_accounts_on_an_empty_fleet() -> None:
    result = await apply_privacy_to_all_accounts(AccountPrivacyUpdateRequest(bio="nobody"))

    assert result.outcomes == []
    assert (result.ok, result.failed, result.skipped) == (0, 0, 0)


@pytest.mark.asyncio
async def test_two_overlapping_sweeps_share_one_process_wide_concurrency_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is per PROCESS, not per request.

    Built inside the coroutine it would cap one sweep, so a double click or a
    second browser tab would reach 8 concurrent ``setPrivacy`` writes — twice the
    pacing the width was chosen for. Nothing else serialises the route.
    """
    for index in range(12):
        account_id = f"acc-{index}"
        await create_account(AccountCreate(account_id=account_id))
        await update_account_status(account_id, status="alive")

    in_flight = 0
    peak = 0

    async def _fake_execute(account_id: str, action: SetPrivacySettings) -> ActionResult:  # noqa: ARG001
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        # Yield so the other waiters get a chance to pile up if the cap is broken.
        await asyncio.sleep(0)
        in_flight -= 1
        return _ok(account_id)

    monkeypatch.setattr("services.accounts.privacy.execute", _fake_execute)

    request = AccountPrivacyUpdateRequest(bio="everybody")
    first, second = await asyncio.gather(
        apply_privacy_to_all_accounts(request),
        apply_privacy_to_all_accounts(request),
    )

    assert peak <= 4
    assert (first.ok, second.ok) == (12, 12)


@pytest.mark.asyncio
async def test_read_account_privacy_translates_a_gateway_not_found_into_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row can vanish between the guard and the read — that is a 404, not a 500.

    ``TelegramAccountNotFoundError`` is unrelated by inheritance to both
    ``TelegramReadError`` and ``AccountNotFoundError``, so without the translation
    it escapes the route's ``service_errors_to_http`` entirely.
    """
    await create_account(AccountCreate(account_id="acc-1"))

    async def _fake_execute_read(account_id: str, action: object) -> object:  # noqa: ARG001
        msg = f"Account not found: {account_id}"
        raise TelegramAccountNotFoundError(msg)

    monkeypatch.setattr("services.accounts.privacy.execute_read", _fake_execute_read)

    with pytest.raises(AccountNotFoundError):
        await read_account_privacy("acc-1")


@pytest.mark.asyncio
async def test_a_partially_applied_fleet_account_reports_which_keys_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare ``failed`` inverted the safety-relevant fact for this feature.

    ``setPrivacy`` is one call per key with no rollback, and the fleet route has no
    per-account SPA re-read to fall back on: an account whose ``profile_photo`` key
    landed before ``bio`` flooded was reported as untouched while its avatar was
    already public.
    """
    await create_account(AccountCreate(account_id="acc-partial"))
    await update_account_status("acc-partial", status="alive")

    async def _partial(account_id: str, _action: SetPrivacySettings) -> ActionResult:
        return ActionResult(
            status="flood_wait",
            action_type="set_privacy_settings",
            account_id=account_id,
            flood_wait_seconds=30,
            applied_privacy_keys=["profile_photo"],
        )

    monkeypatch.setattr("services.accounts.privacy.execute", _partial)

    result = await apply_privacy_to_all_accounts(
        AccountPrivacyUpdateRequest(profile_photo="everybody", bio="everybody"),
    )

    assert result.failed == 1
    outcome = result.outcomes[0]
    assert (outcome.status, outcome.error) == ("failed", "flood_wait")
    assert outcome.applied == ["profile_photo"]
    # The duration too: the error has always carried it, but the outcome dropped it,
    # so the fleet report rendered "retry in ? s" — the one actionable fact.
    assert outcome.retry_after_seconds == 30
