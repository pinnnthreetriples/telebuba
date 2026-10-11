"""Unit tests for services.accounts.mobile_emulator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.emulator.config import EmulatorNotFoundError, NoProxyForMobileError
from schemas.accounts import AccountRead
from schemas.mobile_emulator import OpenMobileResult
from schemas.proxy import ProxySettings
from services.accounts.mobile_emulator import (
    _active_relays,
    open_account_mobile,
    shutdown_mobile_emulator,
)


@pytest.fixture
def mock_account() -> AccountRead:
    return AccountRead(
        account_id="acc-1",
        phone="+12025550199",
        status="alive",
        created_at="now",
        updated_at="now",
        proxy_id="p-1",
    )


@pytest.fixture
def mock_proxy() -> ProxySettings:
    return ProxySettings(
        host="192.168.1.100",
        port=8080,
        proxy_type="socks5",
    )


@pytest.mark.asyncio
async def test_fails_when_account_has_no_proxy(mock_account: AccountRead) -> None:
    with (
        patch("services.accounts.mobile_emulator.require_account", return_value=mock_account),
        patch("services.accounts.mobile_emulator.fetch_account_proxy_settings", return_value=None),
        pytest.raises(NoProxyForMobileError, match="has no assigned proxy"),
    ):
        await open_account_mobile("acc-1")


@pytest.mark.asyncio
async def test_fails_when_emulator_not_installed(
    mock_account: AccountRead,
    mock_proxy: ProxySettings,
) -> None:
    with (
        patch("services.accounts.mobile_emulator.require_account", return_value=mock_account),
        patch(
            "services.accounts.mobile_emulator.fetch_account_proxy_settings",
            return_value=mock_proxy,
        ),
        patch("services.accounts.mobile_emulator.is_emulator_available", return_value=False),
        pytest.raises(EmulatorNotFoundError, match="LDPlayer or ADB binary not located"),
    ):
        await open_account_mobile("acc-1")


@pytest.mark.asyncio
async def test_reuses_already_running_instance(
    mock_account: AccountRead,
    mock_proxy: ProxySettings,
) -> None:
    with (
        patch("services.accounts.mobile_emulator.require_account", return_value=mock_account),
        patch(
            "services.accounts.mobile_emulator.fetch_account_proxy_settings",
            return_value=mock_proxy,
        ),
        patch("services.accounts.mobile_emulator.is_emulator_available", return_value=True),
        patch("services.accounts.mobile_emulator.is_running", return_value=True),
    ):
        res = await open_account_mobile("acc-1")
        assert res.launched is True
        assert res.signed_in is True
        assert res.instance_name == "telebuba_acc-1"
        assert res.message == "Emulator instance already running"


@pytest.mark.asyncio
async def test_full_zero_click_login_flow(
    mock_account: AccountRead,
    mock_proxy: ProxySettings,
) -> None:
    mock_relay = MagicMock()
    mock_relay.start = AsyncMock(return_value=12345)
    mock_relay.aclose = AsyncMock()

    xml_start = '<hierarchy><node text="Start Messaging" bounds="[100,500][300,600]"/></hierarchy>'
    xml_phone = (
        '<hierarchy><node text="Phone number" bounds="[100,200][300,250]"/>'
        '<node text="Next" bounds="[350,500][400,550]"/></hierarchy>'
    )
    xml_2fa = '<hierarchy><node text="Password" bounds="[100,200][300,250]"/></hierarchy>'
    xml_chats = '<hierarchy><node text="Chats" bounds="[50,50][100,100]"/></hierarchy>'

    mock_adb = MagicMock()
    mock_adb.wait_for_boot = AsyncMock(return_value=True)
    mock_adb.set_reverse_proxy = AsyncMock()
    mock_adb.start_app = AsyncMock()
    mock_adb.tap = AsyncMock()
    mock_adb.input_text = AsyncMock()
    mock_adb.press_key = AsyncMock()
    mock_adb.dump_ui_hierarchy = AsyncMock(
        side_effect=[xml_start, xml_phone, xml_2fa, xml_chats, xml_chats]
    )

    with (
        patch("services.accounts.mobile_emulator.require_account", return_value=mock_account),
        patch(
            "services.accounts.mobile_emulator.fetch_account_proxy_settings",
            return_value=mock_proxy,
        ),
        patch("services.accounts.mobile_emulator.is_emulator_available", return_value=True),
        patch("services.accounts.mobile_emulator.is_running", return_value=False),
        patch("services.accounts.mobile_emulator.create_instance", new_callable=AsyncMock),
        patch("services.accounts.mobile_emulator.set_device_profile", new_callable=AsyncMock),
        patch("services.accounts.mobile_emulator.set_resolution", new_callable=AsyncMock),
        patch("services.accounts.mobile_emulator.set_resource_limits", new_callable=AsyncMock),
        patch("services.accounts.mobile_emulator.launch_instance", new_callable=AsyncMock),
        patch("services.accounts.mobile_emulator.LocalProxyRelay", return_value=mock_relay),
        patch("services.accounts.mobile_emulator.ADBDriver", return_value=mock_adb),
        patch(
            "services.accounts.mobile_emulator.poll_for_login_code",
            new_callable=AsyncMock,
            return_value="54321",
        ),
        patch(
            "services.accounts.mobile_emulator.fetch_account_twofa_password",
            new_callable=AsyncMock,
            return_value="cloud_pass_123",
        ),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await open_account_mobile("acc-1")

        assert isinstance(result, OpenMobileResult)
        assert result.launched is True
        assert result.signed_in is True
        assert result.instance_name == "telebuba_acc-1"

        # Verify steps were taken
        mock_relay.start.assert_called_once()
        mock_adb.set_reverse_proxy.assert_called_with(12345)
        mock_adb.start_app.assert_called_with("org.telegram.messenger")
        mock_adb.input_text.assert_any_call("12025550199")
        mock_adb.input_text.assert_any_call("54321")
        mock_adb.input_text.assert_any_call("cloud_pass_123")


@pytest.mark.asyncio
async def test_shutdown_mobile_emulator() -> None:
    mock_relay = MagicMock()
    mock_relay.aclose = AsyncMock()

    _active_relays["acc-99"] = (mock_relay, MagicMock())

    with patch(
        "services.accounts.mobile_emulator.stop_instance", new_callable=AsyncMock
    ) as mock_stop:
        await shutdown_mobile_emulator()
        mock_stop.assert_called_with("telebuba_acc-99")
        mock_relay.aclose.assert_called_once()
        assert "acc-99" not in _active_relays
