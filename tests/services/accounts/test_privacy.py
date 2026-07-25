"""Account-privacy service tests — per-account read/apply and the fleet-wide apply."""

from __future__ import annotations

import pytest

from core.db import create_account, update_account_status
from core.telegram_client import TelegramReadError
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
        return ActionResult(
            status="failed",
            action_type="set_privacy_settings",
            account_id=account_id,
            error_message="privacy_restricted",
        )

    monkeypatch.setattr("services.accounts.privacy.execute", _refused)

    with pytest.raises(AccountActionError, match="privacy_restricted"):
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
    """Unusable sessions are skipped without an RPC; failures never abort the sweep."""
    for account_id in ("acc-alive", "acc-broken", "acc-frozen", "acc-new"):
        await create_account(AccountCreate(account_id=account_id))
    await update_account_status("acc-alive", status="alive")
    await update_account_status("acc-broken", status="alive")
    await update_account_status("acc-frozen", status="frozen")
    # "acc-new" keeps its default ``new`` status — also not usable.

    attempted: list[str] = []

    async def _fake_execute(account_id: str, action: SetPrivacySettings) -> ActionResult:
        attempted.append(account_id)
        assert action.profile_photo == "everybody"
        if account_id == "acc-broken":
            return ActionResult(
                status="failed",
                action_type="set_privacy_settings",
                account_id=account_id,
                error_message="privacy_restricted",
            )
        return _ok(account_id)

    monkeypatch.setattr("services.accounts.privacy.execute", _fake_execute)

    result = await apply_privacy_to_all_accounts(
        AccountPrivacyUpdateRequest(profile_photo="everybody"),
    )

    assert sorted(attempted) == ["acc-alive", "acc-broken"]
    assert (result.ok, result.failed, result.skipped) == (1, 1, 2)
    by_id = {outcome.account_id: outcome for outcome in result.outcomes}
    assert by_id["acc-alive"].status == "ok"
    assert by_id["acc-broken"].status == "failed"
    assert by_id["acc-broken"].error == "privacy_restricted"
    assert by_id["acc-frozen"].status == "skipped"
    assert by_id["acc-frozen"].error is None
    assert by_id["acc-new"].status == "skipped"


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
